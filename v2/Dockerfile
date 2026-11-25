FROM node:24.14.0-bookworm-slim AS ui
WORKDIR /workspace/frontend
COPY frontend/package*.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM eclipse-temurin:25.0.2_10-jdk AS java-build
WORKDIR /workspace/backend
COPY backend/ ./
# Verify the wrapper before execution, independently of the build image's download tools.
ADD --checksum=sha256:4e2fbf6554bc8a4702cdfdd3bef464f423393d784ddbb037216320ce55d5e4e1 https://repo.maven.apache.org/maven2/org/apache/maven/wrapper/maven-wrapper/3.3.4/maven-wrapper-3.3.4.jar .mvn/wrapper/maven-wrapper.jar
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
