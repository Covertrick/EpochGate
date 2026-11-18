"""CBC 预检：只记录当前代码在四条交错上的行为，不实现新的提交协议。"""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import time
from pathlib import Path

import pytest

from epochgate.events import EventLog
from epochgate.gateway import ActionGateway
from epochgate.inventory import InventoryEnvironment
from epochgate.models import (
    AdmissionLease,
    AttemptState,
    CandidateAction,
    CommitResult,
    Task,
    ValidationResult,
)
from epochgate.observation import ObservationStore
from epochgate.registry import Registry
from epochgate.sqlite_inventory import SqliteInventory


def _wire(env: object) -> tuple[EventLog, Registry, ActionGateway]:
    events = EventLog()
    registry = Registry(events, run_id="cbc")
    gateway = ActionGateway(env, registry, ObservationStore(), events, run_id="cbc")  # type: ignore[arg-type]
    return events, registry, gateway


def _open(registry: Registry, task_id: str = "t") -> object:
    now = time.monotonic()
    registry.register_task(
        Task(
            task_id=task_id,
            arrival_mono=now,
            deadline_mono=now + 60.0,
            goal="reserve one unit",
        )
    )
    return registry.begin_attempt(task_id)


def _candidate(attempt: object, action_id: str) -> CandidateAction:
    return CandidateAction(
        action_id=action_id,
        task_id=attempt.task_id,  # type: ignore[attr-defined]
        attempt_id=attempt.attempt_id,  # type: ignore[attr-defined]
        plan_epoch=attempt.plan_epoch,  # type: ignore[attr-defined]
        obs_revision=attempt.obs_revision,  # type: ignore[attr-defined]
        action_type="reserve",
        parameters={"qty": 1},
    )


def _lease(attempt: object) -> AdmissionLease:
    now = time.monotonic()
    return AdmissionLease(
        lease_id=f"lease-{attempt.attempt_id}",  # type: ignore[attr-defined]
        attempt_id=attempt.attempt_id,  # type: ignore[attr-defined]
        earliest_start_mono=now,
        expires_at_mono=now + 60.0,
    )


class _PauseThenEffect(InventoryEnvironment):
    """提交开始后让出事件循环，用来观察撤销能不能插进效果之前。"""

    def __init__(self) -> None:
        super().__init__(stock=3, schedule=[], seed=1)
        self.entered = asyncio.Event()
        self.commits = 0

    async def commit(self, candidate: CandidateAction, conditions: dict) -> CommitResult:
        self.entered.set()
        await asyncio.sleep(0.05)
        self.commits += 1
        return await super().commit(candidate, conditions)


class _RevokeInsideCheck(InventoryEnvironment):
    """检查通过之前先撤销资格。"""

    def __init__(self, gateway: ActionGateway, attempt_id: str) -> None:
        super().__init__(stock=3, schedule=[], seed=1)
        self.gateway = gateway
        self.attempt_id = attempt_id
        self.commits = 0

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        await self.gateway.linearize_revoke(self.attempt_id, reason="revoke during check", bump_plan_epoch=True)
        return ValidationResult(ok=True)

    async def commit(self, candidate: CandidateAction, conditions: dict) -> CommitResult:
        self.commits += 1
        return await super().commit(candidate, conditions)


class _EffectThenRaise(InventoryEnvironment):
    """第一次返回前抛错。同一 action_id 的后续调用能看到内存里的幂等结果。"""

    def __init__(self) -> None:
        super().__init__(stock=3, schedule=[], seed=1)
        self.raised = False

    async def commit(self, candidate: CandidateAction, conditions: dict) -> CommitResult:
        result = await super().commit(candidate, conditions)
        if not self.raised:
            self.raised = True
            raise RuntimeError("died before receipt")
        return result


def test_t1_file_replace_path_is_absent() -> None:
    """仓库没有声明后、os.replace 前的文件发布实现。"""
    root = Path(__file__).resolve().parents[1] / "src" / "epochgate"
    hits = [
        path.name
        for path in root.glob("*.py")
        if "os.replace" in path.read_text(encoding="utf-8")
    ]
    assert hits == []


@pytest.mark.asyncio
async def test_t1_revoke_during_check_never_reaches_effect() -> None:
    """检查和取得提交权之间的撤销会被锁内复查挡住。"""
    events, registry, gateway = _wire(InventoryEnvironment(stock=3, schedule=[], seed=1))
    attempt = _open(registry)
    env = _RevokeInsideCheck(gateway, attempt.attempt_id)
    gateway._env = env  # 探针把同一网关交给检查回调
    result = await gateway.submit(_candidate(attempt, "act-check"), _lease(attempt))
    assert result.accepted is False
    assert env.commits == 0
    assert env.stock == 3
    assert any(event.event_type == "eligibility_revoked" for event in events.iter_events())


@pytest.mark.asyncio
async def test_t1_revoke_after_commit_right_cannot_stop_effect() -> None:
    """提交权已经拿走之后，撤销要等效果函数返回，并且返回假。"""
    env = _PauseThenEffect()
    events, registry, gateway = _wire(env)
    attempt = _open(registry)

    async def _revoke() -> bool:
        await env.entered.wait()
        return await gateway.linearize_revoke(attempt.attempt_id, reason="revoke during effect", bump_plan_epoch=True)

    submit_task = asyncio.create_task(gateway.submit(_candidate(attempt, "act-effect"), _lease(attempt)))
    revoke_task = asyncio.create_task(_revoke())
    result = await submit_task
    revoked = await revoke_task
    assert result.accepted is True
    assert revoked is False
    assert env.commits == 1
    assert env.stock == 2
    assert registry.get_attempt(attempt.attempt_id).state == AttemptState.COMMITTED
    assert any(event.event_type == "action_committed" for event in events.iter_events())
    assert not any(event.event_type == "eligibility_revoked" for event in events.iter_events())


def _process_reserve(db_path: str, action_id: str, output: mp.Queue) -> None:
    async def _run() -> None:
        store = SqliteInventory(db_path)
        events, registry, gateway = _wire(_SqliteGatewayEnv(store))
        attempt = _open(registry, task_id=action_id)
        candidate = _candidate(attempt, action_id)
        result = await gateway.submit(candidate, _lease(attempt))
        output.put(
            {
                "accepted": result.accepted,
                "reasons": list(result.reasons),
                "commit_id": result.commit_id,
            }
        )

    asyncio.run(_run())


class _SqliteGatewayEnv:
    """只把 SQLite 库存的检查和条件提交暴露给网关。"""

    def __init__(self, store: SqliteInventory) -> None:
        self.store = store

    async def check(self, candidate: CandidateAction) -> ValidationResult:
        return await self.store.check(candidate)

    async def commit(self, candidate: CandidateAction, conditions: dict) -> CommitResult:
        version = conditions.get("source_version")
        if version is None:
            _qty, version = await self.store.read_stock()
            conditions = {**conditions, "source_version": version}
        return await self.store.commit_action(candidate, conditions)


def test_t2_two_processes_do_not_share_eligibility(tmp_path: Path) -> None:
    """两个进程各自通过本地资格检查。库存条件更新只让其中一个扣减成功。"""
    db_path = tmp_path / "stock.sqlite"
    store = SqliteInventory(db_path, stock=1)

    async def _setup() -> None:
        await store.setup()

    asyncio.run(_setup())
    ctx = mp.get_context("spawn")
    queue: mp.Queue = ctx.Queue()
    first = ctx.Process(target=_process_reserve, args=(str(db_path), "act-a", queue))
    second = ctx.Process(target=_process_reserve, args=(str(db_path), "act-b", queue))
    first.start()
    second.start()
    first.join(20)
    second.join(20)
    assert first.exitcode == 0
    assert second.exitcode == 0
    reports = [queue.get(timeout=5), queue.get(timeout=5)]
    accepted = [item for item in reports if item["accepted"]]
    rejected = [item for item in reports if not item["accepted"]]
    assert len(accepted) == 1
    assert len(rejected) == 1
    assert any(
        "version conflict" in " ".join(item["reasons"]) or "insufficient" in " ".join(item["reasons"])
        for item in rejected
    )

    async def _stock() -> tuple[int, str]:
        return await SqliteInventory(db_path).read_stock()

    qty, version = asyncio.run(_stock())
    assert qty == 0
    assert version == "v2"


def test_t3_memory_epoch_bump_does_not_survive_new_registry(tmp_path: Path) -> None:
    """资格和 plan_epoch 只在进程内。库存版本没变时，新登记表可以重新接纳。"""
    db_path = tmp_path / "epoch.sqlite"
    store = SqliteInventory(db_path, stock=5)

    async def _run() -> tuple[int, int, bool]:
        await store.setup()
        events, registry, gateway = _wire(_SqliteGatewayEnv(store))
        attempt = _open(registry)
        revoked = await gateway.linearize_revoke(
            attempt.attempt_id,
            reason="supersede before restart",
            bump_plan_epoch=True,
        )
        assert revoked is True
        old_epoch, _revision = registry.current_epoch("t")
        fresh_events, fresh_registry, fresh_gateway = _wire(_SqliteGatewayEnv(store))
        fresh = _open(fresh_registry, task_id="t2")
        result = await fresh_gateway.submit(_candidate(fresh, "act-after-restart"), _lease(fresh))
        qty, _version = await store.read_stock()
        return old_epoch, qty, result.accepted and not any(
            event.event_type == "eligibility_revoked" for event in fresh_events.iter_events()
        )

    old_epoch, qty, accepted = asyncio.run(_run())
    assert old_epoch == 1
    assert accepted is True
    assert qty == 4


@pytest.mark.asyncio
async def test_t4_effect_without_receipt_sticks_and_new_action_id_repeats() -> None:
    """回执前失败时效果已经发生。同一网关不能重试；换一个 action_id 会再扣一次。"""
    env = _EffectThenRaise()
    events, registry, gateway = _wire(env)
    attempt = _open(registry)
    with pytest.raises(RuntimeError, match="died before receipt"):
        await gateway.submit(_candidate(attempt, "act-crash"), _lease(attempt))
    assert env.stock == 2
    assert any(event.event_type == "commit_unknown" for event in events.iter_events())
    assert not any(event.event_type == "action_committed" for event in events.iter_events())

    blocked = await gateway.submit(_candidate(attempt, "act-crash"), _lease(attempt))
    assert blocked.accepted is False
    assert any("attempt not eligible" in reason for reason in blocked.reasons)
    assert env.stock == 2

    _events2, registry2, gateway2 = _wire(env)
    retry = _open(registry2, task_id="t2")
    again = await gateway2.submit(_candidate(retry, "act-crash"), _lease(retry))
    assert again.accepted is True
    assert env.stock == 2

    third = _open(registry2, task_id="t3")
    repeated = await gateway2.submit(_candidate(third, "act-new"), _lease(third))
    assert repeated.accepted is True
    assert env.stock == 1
