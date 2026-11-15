# Built from the repo root (pnpm workspace): docker build -f apps/web/Dockerfile .
FROM node:24-slim AS build
ENV PNPM_HOME=/pnpm PATH=/pnpm:$PATH NEXT_TELEMETRY_DISABLED=1
RUN corepack enable pnpm
WORKDIR /repo
COPY pnpm-workspace.yaml package.json pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/
COPY packages/sdk/package.json packages/sdk/
# Behind a TLS-intercepting proxy, pass the organisation's CA bundle as the corp_ca build
# secret (CORP_CA_BUNDLE, see docker-compose.yml). Node trusts it only through
# NODE_EXTRA_CA_CERTS, for the steps that reach the network: corepack fetching pnpm,
# the install, and the build. It never ends up in the image.
RUN --mount=type=secret,id=corp_ca,required=false     if [ -s /run/secrets/corp_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/corp_ca; fi;     pnpm install --frozen-lockfile --prod=false
COPY apps/web apps/web
COPY packages packages
RUN --mount=type=secret,id=corp_ca,required=false     if [ -s /run/secrets/corp_ca ]; then export NODE_EXTRA_CA_CERTS=/run/secrets/corp_ca; fi;     pnpm --filter web build

FROM node:24-slim AS run
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000
WORKDIR /repo
COPY --from=build /repo /repo
USER node
EXPOSE 3000
WORKDIR /repo/apps/web
CMD ["node", "node_modules/next/dist/bin/next", "start"]
