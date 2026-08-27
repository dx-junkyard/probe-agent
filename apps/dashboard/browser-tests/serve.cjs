// Static dashboard + /api reverse proxy, so the real SPA talks to the real
// Control Server exactly as it does in production (cookies included).
const http = require("node:http");
const fs = require("node:fs");
const path = require("node:path");

const DIST = process.argv[2];
const API = Number(process.argv[3]);
const PORT = Number(process.argv[4]);
const TYPES = { ".html": "text/html", ".js": "text/javascript", ".css": "text/css", ".svg": "image/svg+xml" };

http.createServer((req, res) => {
  if (req.url.startsWith("/api/")) {
    const proxied = http.request(
      { host: "127.0.0.1", port: API, path: req.url.slice(4) || "/", method: req.method, headers: req.headers },
      (up) => { res.writeHead(up.statusCode, up.headers); up.pipe(res); },
    );
    proxied.on("error", (e) => { res.writeHead(502); res.end(String(e)); });
    req.pipe(proxied);
    return;
  }
  const rel = req.url.split("?")[0];
  let file = path.join(DIST, rel === "/" ? "index.html" : rel);
  if (!fs.existsSync(file) || fs.statSync(file).isDirectory()) file = path.join(DIST, "index.html");
  res.writeHead(200, { "Content-Type": TYPES[path.extname(file)] ?? "application/octet-stream" });
  res.end(fs.readFileSync(file));
}).listen(PORT, "127.0.0.1", () => console.log("serving on " + PORT));
