FROM node:22-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine@sha256:4a73073bd557c65b759505da037898b61f1be6cbcc3c2c3aeac22d2a470c1752
COPY --from=build /app/dist /usr/share/nginx/html
# The build stage may emit static assets with a restrictive umask; ensure the
# nginx worker (running as an unprivileged user) can read all of them,
# otherwise requests like GET /favicon.svg return 403 (Issue #106).
RUN chmod -R a+rX /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV PROBE_CLIENT_SERVER_URL=http://localhost:8000
EXPOSE 8501
CMD ["nginx", "-g", "daemon off;"]
