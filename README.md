# Kit Claude

Carpeta lista para usar con Claude Code. Trae 4 skills:

| Comando | Para que sirve |
|---|---|
| `/watch` | Mirar y desarmar un video (archivo o link de Instagram, TikTok, YouTube, Ads Library): escenas, textos en pantalla, lo que se dice, con minuto y segundo. |
| `/grill-me` | Claude te entrevista sobre una idea, una campana o un brief, una pregunta a la vez, y guarda todo en la carpeta `brainstorms/`. |
| `/level-up` | Mejorar un documento incompleto o desactualizado sin inventar datos. |
| `/skill-creator` | Crear tus propias skills. |

## Instalar (una sola vez)

1. **Descargar.** Boton verde **Code** > **Download ZIP**. Descomprimir la carpeta donde quieras (por ejemplo, en Documentos).
2. **Abrir.** En VS Code: **File** > **Open Folder** > elegir la carpeta. Abrir el panel de Claude Code.
3. **Preparar `/watch`.** Escribirle a Claude: `instala lo que necesita /watch`. Va a correr esto (podes correrlo vos en la terminal):
   - Windows:
     ```
     winget install --id Python.Python.3.12 --exact
     winget install --id Gyan.FFmpeg --exact
     winget install --id yt-dlp.yt-dlp --exact
     ```
   - Mac:
     ```
     brew install ffmpeg yt-dlp
     ```
   Despues, **cerrar y volver a abrir VS Code**.

### Opcional: que `/watch` entienda el audio

Sin esto, `/watch` lee subtitulos si el video los trae y analiza la imagen. Con esto, ademas transcribe lo que se dice.

1. Crear una cuenta gratis en [console.groq.com](https://console.groq.com) > **API Keys** > **Create API Key**. Copiar la clave.
2. En la carpeta del kit, duplicar `.env.example` y renombrar la copia a `.env`.
3. Abrir `.env` y pegar la clave despues de `GROQ_API_KEY=`. Guardar.

La clave nunca se pega en el chat.

## Usar

```
/watch https://www.instagram.com/reel/XXXX que hook usa en los primeros 3 segundos
/watch "C:\Users\vos\Videos\anuncio.mp4"
/grill-me campana de verano
/level-up brief-campana.md
/skill-creator quiero una skill que arme guiones de reels
```

Tip para videos largos: pedir un tramo (`del 0:30 al 1:10`) gasta mucho menos que mirar todo.
