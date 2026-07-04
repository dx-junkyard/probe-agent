FROM node:22-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
# The build stage may emit static assets with a restrictive umask; ensure the
# nginx worker (running as an unprivileged user) can read all of them,
# otherwise requests like GET /favicon.svg return 403 (Issue #106).
RUN chmod -R a+rX /usr/share/nginx/html
COPY nginx.conf /etc/nginx/templates/default.conf.template
ENV PROBE_CLIENT_SERVER_URL=http://localhost:8000
EXPOSE 8501
CMD ["nginx", "-g", "daemon off;"]
