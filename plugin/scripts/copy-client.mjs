import { copyFileSync, mkdirSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
const lib = new URL('../lib/', import.meta.url)
mkdirSync(fileURLToPath(lib), { recursive: true })
copyFileSync(new URL('../src/client.js', import.meta.url), new URL('client.js', lib))
