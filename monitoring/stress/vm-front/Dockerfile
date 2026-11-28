# VM do front em um container: o mesmo build do deploy (VITE_API_BASE_URL=/)
# servido pelo nginx, com /api/ repassado para a VM do back.
FROM node:22-alpine AS build
WORKDIR /front
COPY package*.json ./
RUN npm ci --no-audit --no-fund
COPY . .
RUN echo 'VITE_API_BASE_URL=/' > .env.production.local && npm run build

FROM nginx:1.27-alpine
COPY --from=build /front/dist /var/www/akavision
COPY --from=stress nginx.conf /etc/nginx/conf.d/default.conf
