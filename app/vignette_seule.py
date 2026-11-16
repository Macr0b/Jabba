"""Calcule une vignette JPEG : image sur l'entrée standard, JPEG sur la sortie.

Lancé par bac_a_sable.vignette, sans aucun droit : ce programme ne voit que l'image
qu'on lui passe, n'ouvre aucun fichier et n'écrit que sur sa sortie.
"""
import io
import sys

from PIL import Image, ImageOps


def main():
    size = int(sys.argv[1])
    with Image.open(io.BytesIO(sys.stdin.buffer.read())) as image:
        image.draft("RGB", (size, size))
        image = ImageOps.exif_transpose(image)
        image.thumbnail((size, size))
        output = io.BytesIO()
        image.convert("RGB").save(output, "JPEG", quality=80)
    sys.stdout.buffer.write(output.getvalue())


if __name__ == "__main__":
    main()
