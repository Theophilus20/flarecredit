# Reproducible build for Confidential Space  the image digest IS the code
# hash that CreditRegistry whitelists. SOURCE_DATE_EPOCH + trimpath + fixed
# base digests make the build byte-identical for anyone who rebuilds it,
# which is what makes "trust the code, not the operator" auditable.
FROM golang:1.22-bookworm AS build
ENV SOURCE_DATE_EPOCH=0 CGO_ENABLED=0 GOFLAGS="-trimpath -buildvcs=false"
WORKDIR /src
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN go build -ldflags="-s -w -buildid=" -o /enclave .

FROM gcr.io/distroless/static-debian12
COPY --from=build /enclave /enclave
ENTRYPOINT ["/enclave"]
