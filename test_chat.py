import requests

# 1. D'abord, se connecter
login_data = {
    "username": "admin",
    "password": "fastpark123"
}

session = requests.Session()

# Connexion
login_response = session.post(
    "http://localhost:5000/api/login",
    json=login_data
)
print("Login:", login_response.json())

# 2. Poser la question au chat
chat_response = session.post(
    "http://localhost:5000/api/chat",
    json={"question": "Combien de places libres ?"}
)
print("\nChat:", chat_response.json())