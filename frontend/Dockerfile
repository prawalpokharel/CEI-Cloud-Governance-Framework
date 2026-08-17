# Frontend, for local dev compose. Next.js needs the API origin at BUILD
# time (NEXT_PUBLIC_* is inlined into the bundle), which is why it arrives as
# a build arg rather than a runtime variable.
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG NEXT_PUBLIC_CORE_ENGINE_URL=http://localhost:8000
ENV NEXT_PUBLIC_CORE_ENGINE_URL=$NEXT_PUBLIC_CORE_ENGINE_URL
RUN npm run build

EXPOSE 3000
CMD ["npm", "run", "start"]
