import random


numero_secreto = random.randint(1,100)
cant_intentos = 0
cant_max_intentos = 5
adivinado = False

print("Bienvenido al juego de adivnar el número secreto, tienes 10 intentos para adivinar el número secreto")

while not adivinado:
    if not cant_intentos < cant_max_intentos:
        print("Game Over! No has podido adivinar dentro de los 10 intentos")
        break
    entrada = input("Introduce un número del 1 al 99: ") 
    numero = int(entrada)
    if numero == numero_secreto:
        print("Felicidades, has adivinado el número secreto")
        adivinado = True
    elif numero < numero_secreto:
        print("El número es mayor al ingresado")
    else:
        print("El número es menor al ingresado")
    
    cant_intentos += 1 

