import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import test from 'node:test'
import { fileURLToPath } from 'node:url'

const testsDirectory = dirname(fileURLToPath(import.meta.url))
const webRoot = resolve(testsDirectory, '..')
const appPath = join(webRoot, 'src', 'App.tsx')
const markdownPath = join(webRoot, 'src', 'content', 'landing.md')
const indexPath = join(webRoot, 'index.html')
const screensDirectory = join(webRoot, 'public', 'assets', 'screens')

const appSource = readFileSync(appPath, 'utf8')
const landingMarkdown = readFileSync(markdownPath, 'utf8')
const indexHtml = readFileSync(indexPath, 'utf8')

const normalizeText = (value) =>
  value
    .normalize('NFD')
    .replace(/\p{Diacritic}/gu, '')
    .toLocaleLowerCase('pt-BR')

const publicCopy = normalizeText([appSource, landingMarkdown, indexHtml].join('\n'))
const screenAssetNames = readdirSync(screensDirectory)
  .filter((name) => /\.(?:png|jpe?g|webp)$/i.test(name))
  .sort()

const referencedScreenAssets = [
  ...appSource.matchAll(/\/assets\/screens\/([a-z0-9._-]+\.(?:png|jpe?g|webp))/gi),
].map((match) => match[1])

test('usa Markdown como fonte de conteúdo renderizada com suporte a GFM', () => {
  assert.match(landingMarkdown, /^#{1,3}\s+\S+/m, 'landing.md precisa conter ao menos um título')
  assert.ok(landingMarkdown.trim().length >= 120, 'landing.md não pode ser apenas um placeholder')
  assert.match(appSource, /landing\.md\?raw/, 'App deve importar o conteúdo Markdown como texto')
  assert.match(appSource, /<ReactMarkdown\b/, 'App deve renderizar o Markdown com ReactMarkdown')
  assert.match(appSource, /remarkPlugins=\{\[remarkGfm\]\}/, 'App deve habilitar remark-gfm')
})

test('cobre o fluxo e os limites obrigatórios do PRD na cópia pública', () => {
  const requiredConcepts = [
    ['identidade Nexo', /\bnexo\b/],
    ['briefing ou necessidade de compra', /\b(?:briefing|necessidade|requisicao)\b/],
    ['fornecedores ou sourcing', /\b(?:fornecedores?|sourcing)\b/],
    ['envio de RFQ', /\brfqs?\b/],
    ['equalização ou comparação de propostas', /\b(?:equaliz\w*|compar\w*)\b/],
    ['aprovação humana', /\b(?:aprovacao humana|decisao financeira continua humana)\b/],
    ['award', /\baward\b/],
    ['aceite do fornecedor', /\baceite\b/],
    ['reserva de capacidade, data ou horário', /\b(?:reserva|reservad[oa]s?|capacidade reservada)\b/],
    ['evidência ou trilha auditável', /\b(?:evidenc\w*|audit\w*|rastro\w*)\b/],
    ['políticas ou regras determinísticas', /\b(?:politic\w*|regr\w*)\b[^.\n]{0,80}\bdeterministic\w*/],
  ]

  const missing = requiredConcepts
    .filter(([, pattern]) => !pattern.test(publicCopy))
    .map(([label]) => label)

  assert.deepEqual(missing, [], `Conceitos obrigatórios ausentes: ${missing.join(', ')}`)
})

test('mantém um conjunto íntegro e distinto de assets para as três jornadas', () => {
  assert.ok(screenAssetNames.length >= 3, 'São necessários ao menos três mockups de produto')

  const requiredJourneys = [
    ['central de procurement', /procurement-command-center/i],
    ['comparação de propostas', /proposal-comparison/i],
    ['fluxo móvel do fornecedor', /supplier-mobile-flow/i],
  ]

  for (const [label, pattern] of requiredJourneys) {
    assert.ok(
      screenAssetNames.some((name) => pattern.test(name)),
      `Asset ausente para ${label}`,
    )
    assert.ok(
      referencedScreenAssets.some((name) => pattern.test(name)),
      `A landing precisa exibir o asset de ${label}`,
    )
  }

  const hashes = new Set()
  for (const assetName of screenAssetNames) {
    const assetPath = join(screensDirectory, assetName)
    const asset = readFileSync(assetPath)
    assert.ok(statSync(assetPath).size > (assetName.endsWith('.webp') ? 50_000 : 100_000), `${assetName} parece ser um placeholder pequeno`)
    hashes.add(createHash('sha256').update(asset).digest('hex'))

    if (assetName.endsWith('.png')) {
      assert.equal(asset.subarray(0, 8).toString('hex'), '89504e470d0a1a0a', `${assetName} não é um PNG válido`)
      assert.ok(asset.readUInt32BE(16) >= 1200, `${assetName} deve ter ao menos 1200 px de largura`)
      assert.ok(asset.readUInt32BE(20) >= 800, `${assetName} deve ter ao menos 800 px de altura`)
    }
  }

  assert.equal(hashes.size, screenAssetNames.length, 'Assets de tela não podem ser duplicatas binárias')
})

test('todas as referências a telas resolvem para arquivos públicos reais', () => {
  assert.ok(referencedScreenAssets.length >= 3, 'A landing deve referenciar as três jornadas visuais')

  for (const assetName of referencedScreenAssets) {
    assert.ok(
      existsSync(join(screensDirectory, assetName)),
      `Referência quebrada: /assets/screens/${assetName}`,
    )
  }
})

test('cada mockup exibido possui alt text útil e deixa claro que é conceitual', () => {
  const imageTags = appSource.match(/<img\b[\s\S]*?\/>/g) ?? []
  const screenImageTags = imageTags.filter((tag) => /\/assets\/screens\//.test(tag))
  const altTexts = []

  assert.equal(
    screenImageTags.length,
    referencedScreenAssets.length,
    'Cada referência de tela deve estar em um elemento img auditável',
  )

  for (const tag of screenImageTags) {
    const source = tag.match(/src=["']([^"']+)["']/)?.[1] ?? 'tela sem src'
    const alt = tag.match(/alt=["']([^"']+)["']/)?.[1] ?? ''
    const normalizedAlt = normalizeText(alt)

    assert.ok(alt.length >= 40, `${source} precisa de alt text descritivo`)
    assert.match(
      normalizedAlt,
      /\b(?:conceitual|ilustrativ\w*|demonstracao)\b/,
      `${source} deve explicitar no alt text que a tela é conceitual ou ilustrativa`,
    )
    assert.doesNotMatch(normalizedAlt, /^(?:imagem|tela|mockup)\s*\d*$/, `${source} tem alt text genérico`)
    altTexts.push(normalizedAlt)
  }

  assert.equal(new Set(altTexts).size, altTexts.length, 'Cada tela deve ter uma descrição alternativa própria')
  assert.match(publicCopy, /\binterface conceitual\b/, 'A área visual deve informar que a interface é conceitual')
  assert.match(publicCopy, /\bdados ilustrativos\b/, 'A área visual deve informar que os dados são ilustrativos')
})

test('não publica claims incompatíveis com os limites do PRD', () => {
  const prohibitedClaims = [
    ['autonomia total', /\b100%\s+(?:autonom\w*|automatizad\w*)\b/],
    ['execução sem aprovação humana', /\b(?:sem|dispensa\w*)\s+(?:qualquer\s+)?aprovacao humana\b/],
    ['substituição de pessoas ou procurement', /\bsubstitui\w*\s+(?:o\s+|a\s+)?(?:time\s+de\s+)?(?:procurement|comprador\w*|human\w*)\b/],
    ['economia garantida', /\beconomia\s+(?:garantida|comprovada|gerada)\b/],
    ['percentual de economia sem evidência', /\beconom(?:ia|ize)\w*[^.\n]{0,30}\b\d+(?:[.,]\d+)?%/],
    ['resultado real não demonstrado', /\b(?:rfqs?|propostas?|award|aceite|reserva)\s+(?:reais?|confirmad\w*|entregues?)\b/],
    ['dispensa de compliance ou homologação', /\b(?:sem|dispensa\w*|elimina\w*)\s+(?:compliance|homologacao)\b/],
    ['pagamento autônomo', /\b(?:paga|pagamento)\w*\s+(?:automatic\w*|autonom\w*)\b/],
  ]

  const violations = prohibitedClaims
    .filter(([, pattern]) => pattern.test(publicCopy))
    .map(([label]) => label)

  assert.deepEqual(violations, [], `Claims proibidos encontrados: ${violations.join(', ')}`)
})

test('metadados essenciais estão em português e descrevem o produto com honestidade', () => {
  assert.match(indexHtml, /<html\s+lang=["']pt-BR["']>/i)
  assert.match(indexHtml, /<title>[^<]*Nexo[^<]*<\/title>/i)
  assert.match(indexHtml, /<meta\s+name=["']description["'][\s\S]*?content=["'][^"']*evidências[^"']*aprovação humana[^"']*["']/i)
  assert.match(indexHtml, /class=["']skip-link["'][^>]*href=["']#conteudo["']/i)
})
