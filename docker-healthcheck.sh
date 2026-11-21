#!/bin/sh
# Ask under the first allowed host name, because Django rejects any other Host header.
host="${NETATLAS_ALLOWED_HOSTS:-localhost}"
host="${host%%,*}"
host="${host#.}"
[ "$host" = "*" ] && host=localhost
exec curl -fsS -H "Host: $host" http://127.0.0.1:8000/api/status/
