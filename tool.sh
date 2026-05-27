
source .env

curl -sS -X POST http://localhost:8000/tokens \
  -H "Authorization: Bearer ${ADMIN_TOKEN}" \
  -H 'Content-Type: application/json' \
  -d '{"name":"dashboard-admin-token"}'

