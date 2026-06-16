FROM node:20-alpine

WORKDIR /app

COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

# Run the dev server as the built-in non-root `node` user (uid 1000, matching
# the host bind-mount owner). A fresh ui-node-modules volume inherits this
# ownership; an existing root-owned volume should be recreated on rebuild.
RUN chown -R node:node /app
USER node

EXPOSE 5173

ENTRYPOINT ["entrypoint.sh"]
CMD ["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"]
