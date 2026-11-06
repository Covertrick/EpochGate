const name = "eg-policy";
const inject = ["tools"];
const ALLOWED = new Set(["eg_read", "eg_write", "eg_bash"]);

function apply(ctx) {
  ctx.tools.guard((execution) => {
    if (!ALLOWED.has(execution.name)) return "clean path refuses tools outside the container workspace";
    const blob = JSON.stringify(execution.arguments ?? {});
    if (/\.env|gold_patch|reference_recovery|DEEPSEEK_API_KEY|EPOCHGATE_API_KEY|git\s+push/i.test(blob)) {
      return "clean path refuses secrets, gold, and push";
    }
    return undefined;
  });
}

export { apply, inject, name };
