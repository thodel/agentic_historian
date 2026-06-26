import random

def scramble(s):
    chars = list(s)
    random.shuffle(chars)
    return ''.join(chars)

# Test with "hello"
result = scramble("hello")
print(result)