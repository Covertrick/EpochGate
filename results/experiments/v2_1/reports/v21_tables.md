# 确认性汇总

## 操作检验

```json
{
  "harmful_release_mismatch": 0,
  "harmless_g2_false_cancel": 0,
  "harmless_g2_restart": 0,
  "harmless_g1_missing_restart": 0,
  "old_attempt_committed": 0,
  "duplicate_side_effects": 0,
  "arrival_hash_mismatch": 0
}
```

## 有害 ΔW95 = W(G0)−W(G2)

| load | scheduler | rho | n | baseline_mean | delta_mean | ci_low | ci_high | relative_to_baseline | meets_10pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | fcfs | 0.5000 | 50 | 15.3771 | 2.1102 | 1.7125 | 2.4905 | 0.1372 | True |
| high | runtime-plas | 0.5000 | 50 | 15.2761 | 2.0091 | 1.5948 | 2.4207 | 0.1315 | True |
| high | srpt | 0.5000 | 50 | 15.9868 | 2.4211 | 1.9119 | 2.9438 | 0.1514 | True |
| medium | fcfs | 0.5000 | 50 | 11.7964 | 1.8151 | 1.3823 | 2.2523 | 0.1539 | True |
| medium | fcfs | 1.0000 | 50 | 12.8933 | 2.1773 | 1.7312 | 2.6158 | 0.1689 | True |
| medium | fcfs | 2.0000 | 50 | 15.4507 | 2.5811 | 2.2177 | 2.9260 | 0.1671 | True |
| medium | runtime-plas | 0.5000 | 50 | 11.5330 | 1.5517 | 1.1534 | 1.9610 | 0.1345 | True |
| medium | runtime-plas | 1.0000 | 50 | 12.4594 | 1.7433 | 1.3369 | 2.1699 | 0.1399 | True |
| medium | runtime-plas | 2.0000 | 50 | 14.9475 | 2.0779 | 1.5523 | 2.5751 | 0.1390 | True |
| medium | srpt | 0.5000 | 50 | 12.1574 | 1.8252 | 1.3734 | 2.2857 | 0.1501 | True |
| medium | srpt | 1.0000 | 50 | 13.6264 | 2.4473 | 1.9605 | 2.9293 | 0.1796 | True |
| medium | srpt | 2.0000 | 50 | 15.5626 | 1.7411 | 1.1043 | 2.3364 | 0.1119 | True |

## 有害 ΔF95 = F(G0)−F(G2)

| load | scheduler | rho | n | baseline_mean | delta_mean | ci_low | ci_high | relative_to_baseline | meets_10pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| high | fcfs | 0.5000 | 50 | 28.6435 | 2.1522 | 1.7839 | 2.5054 | 0.0751 | False |
| high | runtime-plas | 0.5000 | 50 | 28.5707 | 2.0794 | 1.6924 | 2.4486 | 0.0728 | False |
| high | srpt | 0.5000 | 50 | 31.4114 | 2.5477 | 2.0303 | 3.0972 | 0.0811 | False |
| medium | fcfs | 0.5000 | 50 | 24.9681 | 1.5063 | 1.1672 | 1.8469 | 0.0603 | False |
| medium | fcfs | 1.0000 | 50 | 26.1950 | 1.9932 | 1.6291 | 2.3406 | 0.0761 | False |
| medium | fcfs | 2.0000 | 50 | 28.8774 | 2.5655 | 2.2112 | 2.8839 | 0.0888 | False |
| medium | runtime-plas | 0.5000 | 50 | 24.8998 | 1.4381 | 1.0938 | 1.7892 | 0.0578 | False |
| medium | runtime-plas | 1.0000 | 50 | 25.9204 | 1.7186 | 1.3753 | 2.0464 | 0.0663 | False |
| medium | runtime-plas | 2.0000 | 50 | 28.4276 | 2.1157 | 1.6619 | 2.5393 | 0.0744 | False |
| medium | srpt | 0.5000 | 50 | 27.3706 | 2.1412 | 1.6503 | 2.6431 | 0.0782 | False |
| medium | srpt | 1.0000 | 50 | 29.0804 | 2.9053 | 2.3913 | 3.4195 | 0.0999 | False |
| medium | srpt | 2.0000 | 50 | 31.0447 | 2.0056 | 1.2900 | 2.6417 | 0.0646 | False |

## 无害 ΔW95 = W(G1)−W(G2)

| load | scheduler | rho | n | baseline_mean | delta_mean | ci_low | ci_high | relative_to_baseline | meets_10pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| medium | fcfs | 0.5000 | 50 | 9.9813 | -0.7348 | -0.9649 | -0.5127 | -0.0736 | False |
| medium | fcfs | 1.0000 | 50 | 10.7161 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | False |
| medium | fcfs | 2.0000 | 50 | 12.8696 | 2.1536 | 1.7865 | 2.5115 | 0.1673 | True |
| medium | runtime-plas | 0.5000 | 50 | 9.9813 | -0.7348 | -0.9649 | -0.5127 | -0.0736 | False |
| medium | runtime-plas | 1.0000 | 50 | 10.7161 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | False |
| medium | runtime-plas | 2.0000 | 50 | 12.8696 | 2.1536 | 1.7865 | 2.5115 | 0.1673 | True |
| medium | srpt | 0.5000 | 50 | 10.3323 | -0.8468 | -1.1950 | -0.5217 | -0.0820 | False |
| medium | srpt | 1.0000 | 50 | 11.1791 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | False |
| medium | srpt | 2.0000 | 50 | 13.8215 | 2.6425 | 2.1723 | 3.1072 | 0.1912 | True |

## 无害 ΔF95 = F(G1)−F(G2)

| load | scheduler | rho | n | baseline_mean | delta_mean | ci_low | ci_high | relative_to_baseline | meets_10pct |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| medium | fcfs | 0.5000 | 50 | 23.4618 | -0.7400 | -0.9702 | -0.5209 | -0.0315 | False |
| medium | fcfs | 1.0000 | 50 | 24.2018 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | False |
| medium | fcfs | 2.0000 | 50 | 26.3119 | 2.1101 | 1.7430 | 2.4699 | 0.0802 | False |
| medium | runtime-plas | 0.5000 | 50 | 23.4618 | -0.7400 | -0.9702 | -0.5209 | -0.0315 | False |
| medium | runtime-plas | 1.0000 | 50 | 24.2018 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | False |
| medium | runtime-plas | 2.0000 | 50 | 26.3119 | 2.1101 | 1.7430 | 2.4699 | 0.0802 | False |
| medium | srpt | 0.5000 | 50 | 25.2294 | -0.9456 | -1.3311 | -0.5892 | -0.0375 | False |
| medium | srpt | 1.0000 | 50 | 26.1750 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | False |
| medium | srpt | 2.0000 | 50 | 29.0391 | 2.8641 | 2.3655 | 3.3800 | 0.0986 | False |
