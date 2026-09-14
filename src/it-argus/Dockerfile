# Base image ships Chromium/Firefox/WebKit preinstalled, pinned to the exact `@playwright/test`
# version in package.json. This pairing has to match exactly — the image's bundled browser
# revisions are tied to a specific Playwright release, and a version drift between the two is a
# real, common failure mode (the test runner expects a browser build the image doesn't have).
FROM mcr.microsoft.com/playwright:v1.49.1-jammy

WORKDIR /app

COPY package.json ./
RUN npm install

COPY playwright.config.ts ./
COPY tests ./tests

CMD ["npx", "playwright", "test"]
