"""Calcule une vignette JPEG : image sur l'entrée standard, JPEG sur la sortie.

Lancé par bac_a_sable.vignette, sans aucun droit : ce programme ne voit que l'image
qu'on lui passe, n'ouvre aucun fichier et n'écrit que sur sa sortie.
"""
import io
import sys

from PIL import Image, ImageOps


def main():
    taille = int(sys.argv[1])
    with Image.open(io.BytesIO(sys.stdin.buffer.read())) as im:
        im.draft("RGB", (taille, taille))
        im = ImageOps.exif_transpose(im)
        im.thumbnail((taille, taille))
        sortie = io.BytesIO()
        im.convert("RGB").save(sortie, "JPEG", quality=80)
    sys.stdout.buffer.write(sortie.getvalue())


if __name__ == "__main__":
    main()
