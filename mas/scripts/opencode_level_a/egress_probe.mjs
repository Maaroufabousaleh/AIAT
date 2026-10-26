import net from "node:net";

const targets = [
  { host: "1.1.1.1", port: 443 },
  { host: "8.8.8.8", port: 53 },
];

function attempt({ host, port }) {
  return new Promise((resolve) => {
    const socket = net.createConnection({ host, port });
    let settled = false;
    const finish = (connected, code = "") => {
      if (settled) return;
      settled = true;
      socket.destroy();
      resolve({ host, port, connected, error_code: code });
    };
    socket.setTimeout(1500);
    socket.once("connect", () => finish(true));
    socket.once("timeout", () => finish(false, "TIMEOUT"));
    socket.once("error", (error) => finish(false, error.code || "NETWORK_ERROR"));
  });
}

const results = await Promise.all(targets.map(attempt));
console.log(JSON.stringify({ schema_version: "aiat.level-a-egress-probe.v1", results }));
if (results.some((item) => item.connected)) process.exitCode = 1;
