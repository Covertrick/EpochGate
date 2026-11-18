// Commit-only 是受限对照：门后只检查一次资格，没有 fencing，也没有写入前的第二次校验。

export async function publishRestricted({ arm, hold, admit, write, betweenAdmitAndWrite }) {
  if (arm !== "direct" && arm !== "commit_only") {
    throw new Error("restricted arm");
  }
  await hold();
  if (arm === "direct") {
    write();
    return { status: 0, stdout: "", stderr: "", published: true, arm: "direct", eligibilityChecked: false };
  }
  const admission = await admit();
  if (!admission || admission.allowed !== true) {
    return { status: "withheld", published: false, arm: "commit_only", eligibilityChecked: true };
  }
  if (betweenAdmitAndWrite) await betweenAdmitAndWrite();
  write();
  return {
    status: 0,
    stdout: "",
    stderr: "",
    published: true,
    arm: "commit_only",
    eligibilityChecked: true,
    rechecked: false,
  };
}
