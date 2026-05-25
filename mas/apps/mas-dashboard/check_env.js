const http = require('http');
const server = http.createServer((req, res) => {
  if (req.url === '/env') {
    res.writeHead(200, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({
      DASHBOARD_USERNAME: process.env.DASHBOARD_USERNAME,
      DASHBOARD_PASSWORD_HASH: process.env.DASHBOARD_PASSWORD_HASH ? '[SET]' : '[NOT SET]',
      JWT_SECRET: process.env.JWT_SECRET ? '[SET]' : '[NOT SET]',
      NODE_ENV: process.env.NODE_ENV
    }));
  } else if (req.url === '/login') {
    res.writeHead(200, {'Content-Type': 'text/plain'});
    res.end('Login page');
  }
});
server.listen(9998, () => console.log('Check server on 9998'));
