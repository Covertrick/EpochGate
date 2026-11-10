import { request as httpRequest } from "node:http";
import { request as httpsRequest } from "node:https";

function postJson(url, body) {
  const target = new URL(url);
  const data = Buffer.from(JSON.stringify(body));
  const request = target.protocol === "https:" ? httpsRequest : httpRequest;
  return new Promise((resolve, reject) => {
    const req = request(target, {
      method: "POST",
      headers: {
        "content-type": "application/json",
        "content-length": data.length,
      },
    }, (res) => {
      const chunks = [];
      res.on("data", (chunk) => chunks.push(chunk));
      res.on("end", () => {
        const text = Buffer.concat(chunks).toString("utf8");
        if (res.statusCode >= 400) {
          reject(new Error(text || "control request failed"));
          return;
        }
        resolve(JSON.parse(text));
      });
    });
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

async function admit(base, runId) {
  return postJson(`${base}/runs/${runId}/admit`, {});
}

async function reserve(base, body) {
  return postJson(`${base}/effects/reserve`, body);
}

async function publishFile(base, body) {
  return postJson(`${base}/effects/publish-file`, body);
}

async function publishContainer(base, body) {
  return postJson(`${base}/effects/publish-container`, body);
}

async function startCommand(base, body) {
  return postJson(`${base}/effects/commands/start`, body);
}

async function finishCommand(base, body) {
  return postJson(`${base}/effects/commands/finish`, body);
}

export { admit, finishCommand, postJson, publishContainer, publishFile, reserve, startCommand };
