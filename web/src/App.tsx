import { AnimatePresence, motion, useReducedMotion } from 'framer-motion'
import {
  ArrowRight,
  BadgeCheck,
  Building2,
  Check,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  FileCheck2,
  FileSearch,
  Fingerprint,
  ListChecks,
  LockKeyhole,
  MessageSquareText,
  Route,
  Scale,
  ShieldCheck,
  Store,
  UserCheck,
  X,
  type LucideIcon,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import landingMarkdown from './content/landing.md?raw'

type RevealProps = {
  children: ReactNode
  className?: string
  delay?: number
}

type FlowStep = {
  label: string
  title: string
  copy: string
  proof: string
  icon: LucideIcon
}

const flowSteps: FlowStep[] = [
  {
    label: '01 · Briefing',
    title: 'Descreva a necessidade',
    copy: 'Quantidade, data, local, orçamento, restrições e preferências entram em linguagem natural e viram campos visíveis.',
    proof: 'Campos obrigatórios antes da rodada',
    icon: MessageSquareText,
  },
  {
    label: '02 · Plano',
    title: 'Revise políticas e limites',
    copy: 'Critérios eliminatórios, estratégia de sourcing, temas negociáveis e checkpoint humano ficam claros antes da execução.',
    proof: 'Política editável pelo comprador',
    icon: ListChecks,
  },
  {
    label: '03 · Sourcing',
    title: 'Entenda inclusões e exclusões',
    copy: 'O fluxo proposto consulta a base, combina busca semântica com filtros e sinaliza dados que ainda precisam de confirmação.',
    proof: 'Elegibilidade explicável',
    icon: FileSearch,
  },
  {
    label: '04 · RFQ',
    title: 'Acompanhe a rodada',
    copy: 'Cada fornecedor recebe a mesma demanda estruturada. Envio, abertura, resposta e follow-up dependem de eventos rastreáveis.',
    proof: 'Estado baseado em evento',
    icon: Route,
  },
  {
    label: '05 · Decisão',
    title: 'Compare na mesma base',
    copy: 'Preço, logística, restrições, validade, riscos e evidências são organizados lado a lado antes da aprovação humana.',
    proof: 'Cálculo determinístico',
    icon: Scale,
  },
  {
    label: '06 · Aceite',
    title: 'Registre o resultado externo',
    copy: 'Depois da aprovação, o envio do award referencia a proposta escolhida. O aceite e a reserva ficam registrados no handoff.',
    proof: 'Versão final identificada',
    icon: BadgeCheck,
  },
]

const principles = [
  { icon: Route, label: 'Entrega da RFQ rastreável' },
  { icon: Fingerprint, label: 'Campos críticos com evidência' },
  { icon: UserCheck, label: 'Award condicionado à aprovação' },
]

const controlPillars = [
  {
    icon: FileSearch,
    index: '01',
    title: 'A IA interpreta',
    copy: 'Organiza documentos e mensagens, identifica ambiguidades e prepara a próxima ação.',
  },
  {
    icon: ShieldCheck,
    index: '02',
    title: 'As regras limitam',
    copy: 'Orçamento, critérios eliminatórios e temas negociáveis permanecem explícitos.',
  },
  {
    icon: UserCheck,
    index: '03',
    title: 'Uma pessoa decide',
    copy: 'O agente pode recomendar. Aprovar gasto e assumir compromisso continuam fora da sua alçada.',
  },
  {
    icon: Fingerprint,
    index: '04',
    title: 'Eventos confirmam',
    copy: 'Enviado, recebido, aprovado e aceito só mudam de estado após um evento correspondente.',
  },
]

function Reveal({ children, className = '', delay = 0 }: RevealProps) {
  const reduceMotion = Boolean(useReducedMotion())

  return (
    <motion.div
      className={className}
      initial={reduceMotion ? false : { opacity: 0, y: 8 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.18 }}
      transition={{ duration: reduceMotion ? 0 : 0.48, delay, ease: [0.22, 1, 0.36, 1] }}
    >
      {children}
    </motion.div>
  )
}

function Brand() {
  return (
    <a className="brand" href="#inicio" aria-label="Nexo — início">
      <img
        className="brand-logo"
        src="/assets/brand/nexo/svg/nexo-logo-horizontal.svg"
        alt=""
        aria-hidden="true"
      />
    </a>
  )
}

function VisualLabel({ note = 'Dados ilustrativos' }: { note?: string }) {
  return (
    <div className="visual-label">
      <span><i /> Interface conceitual</span>
      <span>{note}</span>
    </div>
  )
}

function SectionIntro({
  eyebrow,
  title,
  copy,
  inverse = false,
}: {
  eyebrow: string
  title: string
  copy: string
  inverse?: boolean
}) {
  return (
    <Reveal className={`section-intro${inverse ? ' section-intro-inverse' : ''}`}>
      <p className="eyebrow">{eyebrow}</p>
      <h2>{title}</h2>
      <p className="section-lead">{copy}</p>
    </Reveal>
  )
}

export default function App() {
  const reduceMotion = Boolean(useReducedMotion())
  const [activeStep, setActiveStep] = useState(0)
  const [evidenceOpen, setEvidenceOpen] = useState(false)

  const activeFlow = flowSteps[activeStep]
  const ActiveFlowIcon = activeFlow.icon

  return (
    <div className="min-h-screen bg-[var(--canvas)] text-[var(--ink)]">
      <header className="site-header">
        <Brand />
        <nav className="desktop-nav" aria-label="Navegação principal">
          <a href="#como-funciona">Como funciona</a>
          <a href="#produto">Produto</a>
          <a href="#controle">Controle</a>
          <a href="#fornecedor">Para fornecedores</a>
        </nav>
        <a className="header-cta" href="#como-funciona">
          Conhecer o fluxo <ArrowRight size={16} aria-hidden="true" />
        </a>
      </header>

      <main id="conteudo">
        <section id="inicio" className="hero-section section-anchor">
          <div className="precision-grid" aria-hidden="true" />
          <div className="hero-layout">
            <div className="hero-copy">
              <motion.p
                initial={reduceMotion ? false : { opacity: 0, y: 8 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.45 }}
                className="eyebrow"
              >
                Procurement com ações verificáveis
              </motion.p>
              <motion.h1
                initial={reduceMotion ? false : { opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.58, delay: 0.05 }}
              >
                Da necessidade à cotação aceita — com <em>evidências</em> em cada decisão.
              </motion.h1>
              <motion.p
                initial={reduceMotion ? false : { opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.58, delay: 0.1 }}
                className="hero-lead"
              >
                Um fluxo único para transformar um briefing corporativo em propostas comparáveis, aprovação humana e aceite do fornecedor — com fontes, limites e pendências visíveis.
              </motion.p>
              <motion.div
                initial={reduceMotion ? false : { opacity: 0, y: 12 }}
                animate={{ opacity: 1, y: 0 }}
                transition={{ duration: reduceMotion ? 0 : 0.58, delay: 0.15 }}
                className="hero-actions"
              >
                <a className="button button-primary" href="#como-funciona">
                  Conhecer o fluxo <ArrowRight size={18} aria-hidden="true" />
                </a>
                <a className="button button-ghost" href="#produto">Explorar a interface conceitual</a>
              </motion.div>
            </div>
            <div className="hero-side-note" aria-label="Escopo desta apresentação">
              <span>Vertical inicial</span>
              <strong>Alimentação para eventos corporativos</strong>
              <small>São Paulo · cenário de demonstração</small>
            </div>
          </div>

          <motion.figure
            initial={reduceMotion ? false : { opacity: 0, y: 18 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.7, delay: 0.18, ease: [0.22, 1, 0.36, 1] }}
            className="product-frame hero-product"
          >
            <VisualLabel />
            <img
              src="/assets/screens/procurement-command-center-v3.webp"
              width="1568"
              height="1003"
              fetchPriority="high"
              decoding="async"
              alt="Interface conceitual do cockpit do Nexo, com trilha de ações, evidências e checkpoint de aprovação humana para uma cotação ilustrativa."
            />
            <figcaption>
              <span><b>O agente age.</b> A timeline mostra o que aconteceu.</span>
              <dl>
                <div><dt>Estado</dt><dd>Por evento</dd></div>
                <div><dt>Decisão</dt><dd>Humana</dd></div>
                <div><dt>Rastro</dt><dd>Por ação</dd></div>
              </dl>
            </figcaption>
          </motion.figure>
        </section>

        <section className="principles-strip" aria-label="Princípios do produto">
          <p>Princípios do produto</p>
          {principles.map(({ icon: Icon, label }) => (
            <div key={label}><Icon size={18} aria-hidden="true" /><span>{label}</span></div>
          ))}
        </section>

        <section className="problem-section section-shell section-anchor" id="problema">
          <SectionIntro
            eyebrow="Duas pontas · um processo"
            title="Comprar ainda exige juntar mensagens, PDFs e planilhas. Vender também."
            copy="O Nexo foi concebido para conectar uma demanda corporativa estruturada a fornecedores que já operam com os materiais e canais que conhecem."
          />
          <div className="problem-grid">
            <Reveal className="problem-card buyer-card">
              <div className="problem-icon"><Building2 size={23} aria-hidden="true" /></div>
              <p className="card-kicker">Para quem compra</p>
              <h3>Cotação fragmentada</h3>
              <p>Contatos antigos, mensagens repetidas, condições ambíguas e comparações manuais dificultam encontrar novas opções e justificar uma decisão.</p>
              <ul>
                <li><X size={16} aria-hidden="true" /> Follow-ups manuais</li>
                <li><X size={16} aria-hidden="true" /> Propostas em formatos diferentes</li>
                <li><X size={16} aria-hidden="true" /> Evidências espalhadas</li>
              </ul>
            </Reveal>
            <div className="problem-connector" aria-hidden="true"><span /><i /><span /></div>
            <Reveal className="problem-card supplier-card" delay={0.06}>
              <div className="problem-icon"><Store size={23} aria-hidden="true" /></div>
              <p className="card-kicker">Para quem fornece</p>
              <h3>Capacidade que o mercado não enxerga</h3>
              <p>Pequenos fornecedores podem atender empresas, mas raramente têm catálogo normalizado, integração técnica ou tempo para portais pesados.</p>
              <ul>
                <li><Check size={16} aria-hidden="true" /> PDF, imagem ou planilha</li>
                <li><Check size={16} aria-hidden="true" /> Resposta por link simples</li>
                <li><Check size={16} aria-hidden="true" /> Sem migrar a operação</li>
              </ul>
            </Reveal>
          </div>
        </section>

        <section id="como-funciona" className="flow-section section-anchor">
          <div className="section-shell">
            <SectionIntro
              inverse
              eyebrow="Fluxo ponta a ponta"
              title="Um briefing. Seis etapas verificáveis."
              copy="No fluxo proposto, cada etapa produz dados estruturados, eventos rastreáveis e um próximo passo que respeita as políticas da compra."
            />
            <div className="flow-workspace">
              <ol className="flow-nav" aria-label="Etapas do fluxo de procurement">
                {flowSteps.map((step, index) => (
                  <li key={step.label}>
                    <button
                      type="button"
                      className={index === activeStep ? 'active' : ''}
                      aria-pressed={index === activeStep}
                      onClick={() => setActiveStep(index)}
                    >
                      <span>{String(index + 1).padStart(2, '0')}</span>
                      <b>{step.label.split(' · ')[1]}</b>
                      {index === activeStep && <motion.i layoutId="flow-active" />}
                    </button>
                  </li>
                ))}
              </ol>
              <div className="flow-detail" aria-live="polite">
                <AnimatePresence mode="wait" initial={false}>
                  <motion.div
                    key={activeStep}
                    initial={reduceMotion ? false : { opacity: 0, x: 8 }}
                    animate={{ opacity: 1, x: 0 }}
                    exit={reduceMotion ? undefined : { opacity: 0, x: -8 }}
                    transition={{ duration: reduceMotion ? 0 : 0.22 }}
                  >
                    <div className="flow-detail-icon"><ActiveFlowIcon size={27} aria-hidden="true" /></div>
                    <p>{activeFlow.label}</p>
                    <h3>{activeFlow.title}</h3>
                    <p className="flow-copy">{activeFlow.copy}</p>
                    <div className="flow-proof"><BadgeCheck size={17} aria-hidden="true" /> {activeFlow.proof}</div>
                  </motion.div>
                </AnimatePresence>
                <div className="flow-counter"><span>{String(activeStep + 1).padStart(2, '0')}</span> / 06</div>
              </div>
            </div>
          </div>
        </section>

        <section id="produto" className="product-section section-shell section-anchor">
          <div className="split-heading">
            <SectionIntro
              eyebrow="Decisão explicável"
              title="A recomendação pode ser auditada."
              copy="A interface foi desenhada para separar fatos confirmados, cálculo determinístico, interpretação da IA, riscos e pendências — antes de qualquer decisão financeira."
            />
            <div className="signal-stack" aria-label="O que a comparação deve tornar visível">
              <div><span>01</span><p><b>Preço comparável</b><small>Total e por pessoa</small></p></div>
              <div><span>02</span><p><b>Critérios objetivos</b><small>Pesos configuráveis</small></p></div>
              <div><span>03</span><p><b>Riscos abertos</b><small>Sem esconder pendências</small></p></div>
            </div>
          </div>
          <Reveal>
            <figure className="product-frame comparison-frame">
              <VisualLabel note="Exemplo visual independente" />
              <img
                src="/assets/screens/proposal-comparison-v2.webp"
                width="1568"
                height="1003"
                loading="lazy"
                decoding="async"
                alt="Interface conceitual ilustrativa de comparação de propostas, com evidências, painel de riscos e ação explícita de aprovação humana."
              />
              <figcaption>
                Exploração visual. Na implementação funcional, pesos e critérios devem seguir a política configurada para cada processo.
              </figcaption>
            </figure>
          </Reveal>

          <Reveal className="accessible-matrix" delay={0.04}>
            <div className="matrix-heading">
              <div>
                <p className="card-kicker">Matriz sem caixa-preta</p>
                <h3>Critérios sugeridos pelo PRD</h3>
              </div>
              <span>Cenário ilustrativo</span>
            </div>
            <div className="table-scroll" tabIndex={0} aria-label="Tabela de pesos sugeridos para comparação">
              <table>
                <thead><tr><th>Critério</th><th>Peso inicial</th><th>O que verificar</th></tr></thead>
                <tbody>
                  <tr><td>Preço total</td><td>35%</td><td>Total e custos adicionais</td></tr>
                  <tr><td>Restrições</td><td>20%</td><td>Atendimento obrigatório</td></tr>
                  <tr><td>Adequação</td><td>15%</td><td>Itens e quantidade</td></tr>
                  <tr><td>Logística</td><td>10%</td><td>Região e horário</td></tr>
                  <tr><td>Demais critérios</td><td>20%</td><td>Prazo, sustentabilidade, documentação e histórico</td></tr>
                </tbody>
              </table>
            </div>
          </Reveal>
        </section>

        <section id="controle" className="control-section section-anchor">
          <div className="section-shell">
            <SectionIntro
              inverse
              eyebrow="Controle · não caixa-preta"
              title="Autonomia para o repetitivo. Limites para o que compromete a empresa."
              copy="A IA interpreta. Políticas e regras determinísticas controlam orçamento, elegibilidade, score e negociação. Uma pessoa aprova antes do award."
            />
            <div className="control-grid">
              {controlPillars.map(({ icon: Icon, index, title, copy }, itemIndex) => (
                <Reveal key={title} className="control-card" delay={itemIndex * 0.045}>
                  <div><Icon size={22} aria-hidden="true" /><span>{index}</span></div>
                  <h3>{title}</h3>
                  <p>{copy}</p>
                </Reveal>
              ))}
            </div>
            <Reveal className="policy-panel">
              <div className="policy-code" aria-label="Exemplo ilustrativo de política de negociação">
                <div><span /><span /><span /><b>policy.yml</b></div>
                <pre><code>{`negotiation:\n  enabled: true\n  maximum_rounds: 2\n  allowed_topics:\n    - total_price\n    - delivery_fee\n  forbidden_actions:\n    - invent_competing_offer\n    - commit_without_approval`}</code></pre>
              </div>
              <div className="policy-copy">
                <p className="card-kicker">Checkpoint humano</p>
                <h3>O agente prepara. A política autoriza. Uma pessoa decide.</h3>
                <p>O backend de domínio deve ser a fonte de verdade. A LLM solicita ações tipadas; ela não escreve estados, altera requisitos ou aprova o próprio plano.</p>
                <button
                  type="button"
                  className="evidence-button"
                  aria-expanded={evidenceOpen}
                  onClick={() => setEvidenceOpen((open) => !open)}
                >
                  <Fingerprint size={18} aria-hidden="true" />
                  {evidenceOpen ? 'Fechar exemplo de evidência' : 'Abrir exemplo de evidência'}
                  <ChevronRight className={evidenceOpen ? 'rotated' : ''} size={18} aria-hidden="true" />
                </button>
                <AnimatePresence initial={false}>
                  {evidenceOpen && (
                    <motion.dl
                      className="evidence-drawer"
                      initial={reduceMotion ? false : { opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={reduceMotion ? undefined : { opacity: 0, height: 0 }}
                    >
                      <div><dt>Campo</dt><dd>Emissão de nota fiscal</dd></div>
                      <div><dt>Status</dt><dd><BadgeCheck size={14} aria-hidden="true" /> Confirmado pelo fornecedor</dd></div>
                      <div><dt>Origem</dt><dd>Resposta à RFQ · versão 2</dd></div>
                      <div><dt>Validade</dt><dd>Até o encerramento da rodada</dd></div>
                    </motion.dl>
                  )}
                </AnimatePresence>
              </div>
            </Reveal>
          </div>
        </section>

        <section id="fornecedor" className="supplier-section section-shell section-anchor">
          <div className="supplier-layout">
            <Reveal className="supplier-copy">
              <p className="eyebrow">Experiência do fornecedor</p>
              <h2>O fornecedor entra com o que já tem.</h2>
              <p className="section-lead">Cardápio em PDF, imagem, planilha, proposta antiga ou texto copiado de uma conversa podem iniciar o perfil comercial.</p>
              <ol className="supplier-steps">
                <li><span>01</span><div><b>Envie o material</b><p>O documento original permanece associado aos campos extraídos.</p></div></li>
                <li><span>02</span><div><b>Revise a fonte</b><p>Confirme, corrija ou marque um campo como não aplicável.</p></div></li>
                <li><span>03</span><div><b>Responda pelo celular</b><p>Preço, disponibilidade, restrições e condições em um link simples.</p></div></li>
                <li><span>04</span><div><b>Registre o aceite</b><p>Após o award aprovado, termos e reserva entram no handoff.</p></div></li>
              </ol>
              <div className="supplier-note"><LockKeyhole size={18} aria-hidden="true" /> Sem storefront, catálogo público ou portal corporativo pesado.</div>
            </Reveal>
            <Reveal className="supplier-visual" delay={0.07}>
              <figure className="product-frame supplier-frame">
                <VisualLabel note="Exemplo visual independente" />
                <img
                  src="/assets/screens/supplier-mobile-flow.webp"
                  width="1536"
                  height="1024"
                  loading="lazy"
                  decoding="async"
                  alt="Interface conceitual ilustrativa do fluxo móvel do fornecedor para revisar uma fonte, corrigir dados e registrar aceite após aprovação."
                />
                <figcaption>Revisão simples, confirmação explícita e reserva somente após o checkpoint humano.</figcaption>
              </figure>
            </Reveal>
          </div>
        </section>

        <section className="markdown-section section-anchor" id="principios">
          <div className="section-shell markdown-layout">
            <div className="markdown-aside">
              <p className="eyebrow">Princípios do PRD</p>
              <span>Conteúdo mantido em Markdown e renderizado na interface.</span>
            </div>
            <Reveal className="markdown-content">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{landingMarkdown}</ReactMarkdown>
            </Reveal>
          </div>
        </section>

        <section className="result-section section-shell section-anchor" id="resultado">
          <Reveal className="result-card">
            <div className="result-copy">
              <p className="eyebrow">Resultado do fluxo</p>
              <h2>Cotação aceita. Evidências reunidas. Próximo passo claro.</h2>
              <p>O handoff proposto reúne a versão final da proposta, os registros usados na decisão, o aceite e a reserva — além do que a empresa ainda precisa tratar.</p>
              <div className="result-disclaimer"><CircleAlert size={18} aria-hidden="true" /> Cenário ilustrativo. Pagamento, contrato e homologação ficam fora do MVP.</div>
            </div>
            <div className="result-status" aria-label="Exemplo ilustrativo de handoff">
              <p><Clock3 size={17} aria-hidden="true" /> CA-024 · Handoff</p>
              <ul>
                <li><CheckCircle2 size={18} aria-hidden="true" /><span>Fornecedor selecionado</span><b>Registro disponível</b></li>
                <li><CheckCircle2 size={18} aria-hidden="true" /><span>Proposta final</span><b>Versão identificada</b></li>
                <li><CheckCircle2 size={18} aria-hidden="true" /><span>Data e capacidade</span><b>Reserva registrada</b></li>
                <li><FileCheck2 size={18} aria-hidden="true" /><span>Evidências</span><b>Reunidas na trilha</b></li>
                <li className="pending"><CircleAlert size={18} aria-hidden="true" /><span>Homologação</span><b>Pendente</b></li>
              </ul>
              <div><span>Status</span><strong>Pronto para contratação</strong></div>
            </div>
          </Reveal>
        </section>

        <section className="final-cta section-shell" id="demo">
          <Reveal>
            <div className="cta-route" aria-hidden="true"><i /><i /><i /><i /></div>
            <p className="eyebrow">Próxima conversa</p>
            <h2>Quer avaliar esse fluxo com uma necessidade real?</h2>
            <p>Use a interface conceitual para discutir como um briefing pode virar uma rodada de cotação controlada, sem perder o checkpoint humano.</p>
            <div className="hero-actions">
              <a className="button button-primary" href="#inicio">Rever a experiência <ArrowRight size={18} aria-hidden="true" /></a>
              <a className="button button-ghost" href="#fornecedor">Conhecer o fluxo do fornecedor</a>
            </div>
            <small>Demonstração conceitual. Capacidades dependem do estágio validado do MVP.</small>
          </Reveal>
        </section>
      </main>

      <footer className="site-footer">
        <Brand />
        <p>Um agente de procurement desenhado para transformar fornecedores desestruturados em opções compráveis.</p>
        <div><a href="#principios">Princípios</a><a href="#controle">Controle</a><a href="#inicio">Voltar ao topo</a></div>
      </footer>
    </div>
  )
}
