// Test script to verify env loading in standalone mode
const path = require('path');
process.env.NODE_ENV = 'production';
process.chdir(path.join(__dirname, '.next', 'standalone'));
console.log('CWD:', process.cwd());

// Now require next which will load the route
const http = require('http');
const server = http.createServer((req, res) => {
  if (req.url === '/debug_env') {
    res.writeHead(200, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({
      DASHBOARD_USERNAME: process.env.DASHBOARD_USERNAME,
      DASHBOARD_PASSWORD_HASH: process.env.DASHBOARD_PASSWORD_HASH ? '[SET]' : '[NOT SET]',
      JWT_SECRET: process.env.JWT_SECRET ? '[SET]' : '[NOT SET]',
      NODE_ENV: process.env.NODE_ENV,
      cwd: process.cwd()
    }));
  }
});
server.listen(3999, () => console.log('Debug server on 3999'));
