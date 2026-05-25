import { createServer } from 'http';
import bcrypt from 'bcryptjs';
import fs from 'fs';

const env = fs.readFileSync('.env', 'utf8');
const hash = env.match(/DASHBOARD_PASSWORD_HASH=(.+)/)?.[1]?.trim();
const username = env.match(/DASHBOARD_USERNAME=(.+)/)?.[1]?.trim();

console.log('Hash:', JSON.stringify(hash));
console.log('Username:', username);
console.log('compare admin:', bcrypt.compareSync('admin', hash));

const server = createServer(async (req, res) => {
  if (req.url === '/test') {
    const hash2 = process.env.DASHBOARD_PASSWORD_HASH;
    const username2 = process.env.DASHBOARD_USERNAME;
    res.writeHead(200, {'Content-Type': 'application/json'});
    res.end(JSON.stringify({ hash: hash2, username: username2 }));
  }
});

server.listen(9999, () => {
  console.log('Debug server on 9999');
});
