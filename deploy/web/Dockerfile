FROM nginx:stable-alpine
COPY deploy/web/nginx.conf /etc/nginx/nginx.conf
RUN mkdir -p /var/cache/nginx/responses
EXPOSE 8080
