# Frontend, for local dev compose. Next.js needs the API origin at BUILD
# time (NEXT_PUBLIC_* is inlined into the bundle), which is why it arrives as
# a build arg rather than a runtime variable.
FROM node:20-slim

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci

COPY . .

ARG NEXT_PUBLIC_CORE_ENGINE_URL=http://localhost:8000
# The legacy-backend origin. Locally the core engine serves those paths too
# (see core-engine/src/routers/compat.py), so both point at the same place;
# production overrides this with the hosted backend's URL.
ARG NEXT_PUBLIC_API_URL=http://localhost:8000
ENV NEXT_PUBLIC_CORE_ENGINE_URL=$NEXT_PUBLIC_CORE_ENGINE_URL
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN npm run build

EXPOSE 3000
CMD ["npm", "run", "start"]
