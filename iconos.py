from PIL import Image
img = Image.open("static/icono.png")
img.resize((192, 192)).save("static/icono-192.png")
img.resize((512, 512)).save("static/icono-512.png")