import bcrypt
password = "libelula"
hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt())
print(hash.decode("utf-8"))