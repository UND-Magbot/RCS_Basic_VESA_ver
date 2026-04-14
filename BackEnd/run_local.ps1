$env:DB_HOST="192.168.0.21"
$env:DB_USER="root"
$env:DB_PASSWORD="1234"
$env:DB_NAME="rcs_basic_db"
uvicorn app.main:app --reload --host 0.0.0.0 --port 8002
