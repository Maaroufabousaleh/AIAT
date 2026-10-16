// Credential inspection and unauthenticated debug endpoints were removed.
// Use the dashboard's authenticated test suite for login diagnostics.
console.error('This legacy credential-inspection helper is disabled. Run npm run test:auth instead.');
process.exitCode = 1;
