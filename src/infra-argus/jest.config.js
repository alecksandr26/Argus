module.exports = {
  testEnvironment: 'node',
  roots: ['<rootDir>/test'],
  testMatch: ['**/*.test.ts'],
  transform: {
    '^.+\\.tsx?$': 'ts-jest',
  },
  // Synth stages Docker build contexts into a temp dir; first run can be slow on /mnt/c.
  testTimeout: 120000,
}
