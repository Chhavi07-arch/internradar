FROM node:24.14.0-bookworm-slim AS ui
WORKDIR /workspace/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM eclipse-temurin:25.0.2_10-jdk AS java-build
WORKDIR /workspace/backend
COPY backend/ ./
RUN chmod +x mvnw

FROM java-build AS web-build
COPY --from=ui /workspace/frontend/dist /workspace/frontend/dist
RUN ./mvnw -B -pl web -am package -DskipTests

FROM java-build AS research-build
RUN ./mvnw -B -pl research-worker -am package -DskipTests

FROM eclipse-temurin:25.0.2_10-jre AS runtime
WORKDIR /app
USER 10001:10001
ENTRYPOINT ["java", "-jar", "/app/service.jar"]

FROM runtime AS web
COPY --from=web-build /workspace/backend/web/target/internradar-web-0.1.0-SNAPSHOT.jar /app/service.jar
EXPOSE 8080

FROM runtime AS research-worker
COPY --from=research-build /workspace/backend/research-worker/target/internradar-research-worker-0.1.0-SNAPSHOT.jar /app/service.jar
