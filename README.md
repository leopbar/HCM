# HCM Lab — Simulador de Gestão de Pessoas

Uma espécie de ERP de RH, inspirado nos principais sistemas de gestão de pessoas do mercado, feito para treinar a parametrização e a operação dos processos de RH: cadastros, férias, workflows de aprovação, ponto, folha, benefícios e recrutamento, com ambientes de homologação e produção. Usa dados fictícios. Não calcula folha legal, verbas rescisórias, INSS, IRRF ou FGTS. Não se conecta ao eSocial nem a serviços governamentais.

## Requisitos

- Docker Desktop com Docker Compose ativo.
- Portas locais 3000, 8000 e 5433 livres. Todas ficam presas a `127.0.0.1`, ou seja, acessíveis apenas deste computador.

## Primeira instalação

```powershell
Copy-Item .env.example .env
```

1. Edite o `.env`: troque `POSTGRES_PASSWORD` e `APP_SECRET` por valores aleatórios. O backend não inicia com segredo ausente ou fraco.
2. Suba os serviços:

```powershell
docker compose up -d --build
```

3. Crie o único administrador:

```powershell
docker compose exec backend python -m app.bootstrap_admin --email seu.email@empresa.com.br
```

O comando mostra uma senha temporária uma única vez. Ela não fica gravada em nenhum arquivo, e a troca é exigida no primeiro acesso. O mesmo comando também:

- redefine o administrador, se ele já existir;
- desativa as demais contas, sem apagá-las, para preservar o histórico de aprovações. Para manter as outras contas, use `--keep-others`.

Endereços:

- Interface: http://localhost:3000
- API: http://localhost:8000. O Swagger em `/docs` fica desligado; para ligar em desenvolvimento, use `ENABLE_API_DOCS=true`.

Não há contas pré-definidas. O administrador cria as contas em **Usuários e acessos** com uma senha temporária, e cada pessoa define a própria senha no primeiro login.

## Segurança

- **Sessão:** fica em cookie `HttpOnly` e `SameSite=Strict`, registrada no servidor, com validade de 8 horas e expiração após 60 minutos de inatividade.
  - **Logout:** invalida a sessão imediatamente.
  - **Troca de senha ou desativação da conta:** encerra todas as sessões da pessoa.
- **CSRF:** requisições com cookie precisam do cabeçalho `X-Requested-With: HCMLab`, que só a origem permitida no CORS consegue enviar.
- **Política de senha:**
  - mínimo de 12 caracteres, com maiúscula, minúscula, número e símbolo;
  - não pode conter o nome nem o e-mail;
  - armazenada com PBKDF2-SHA256 (310 mil iterações);
  - validade de 90 dias (`PASSWORD_MAX_AGE_DAYS`).
- **Bloqueio de conta:** 5 senhas erradas bloqueiam a conta por 15 minutos. O administrador pode desbloquear antes em **Usuários e acessos**. Há também um limite de tentativas de login por IP.
- **Cabeçalhos de segurança:** CSP, `X-Frame-Options`, `nosniff`, `Referrer-Policy`. O CORS libera apenas métodos e cabeçalhos explícitos.
- **Contêineres:** o backend roda como usuário sem privilégios, e o banco e as portas ficam restritos ao computador local.

## O que o sistema cobre

| Área | No HCM Lab |
| --- | --- |
| Cadastro de empresa, estrutura e funcionários | CNPJ e CPF com dígito verificador, UF válida, gestor imediato (sem ciclos), tipo de contrato, dados pessoais, ficha com histórico, renomear e excluir estrutura sem uso |
| Configurações por Empresa: Contratação | Experiência, exigência da tabela salarial, do quadro de vagas e controle por posição. A admissão aprovada cria o funcionário |
| Configurações por Empresa: Férias / Recesso | Período aquisitivo e concessivo, até 3 parcelas (uma com 14 dias ou mais, nenhuma com menos de 5), abono pecuniário de até 1/3, bloqueio de início nos 2 dias antes de feriado ou DSR, aviso com 30 dias e ciência do colaborador. Recesso proporcional para estagiários |
| Configurações por Empresa: Rescisão / Aviso Prévio | Motivos, aviso trabalhado, indenizado ou dispensado, aviso proporcional (Lei 12.506), desligamento com bloqueio do acesso |
| Configurações por Empresa: Vale-Transporte | Linhas e tarifas, solicitação, desconto de até 6% calculado na folha |
| Configurações por Empresa: Posição, Quadro de Vagas, Tabela Salarial | Cadastros em **Cargos, vagas e organograma**, usados por admissão, movimentação e requisição |
| Processos Parametrizáveis | Dependentes, Alteração de dados pessoais, Afastamento/Ausência, Movimentação (movimentar, ceder ou buscar colaborador), Requisição de pessoal, Rescisão, Recesso, Vale-transporte, Lançamento de valores, Férias e Benefícios |
| Atribuições de Seleção de Colaboradores | Próprio solicitante, subordinados diretos, indiretos ou por visibilidade, por processo e grupo do solicitante |
| Configurações nomeadas e Atribuições de Configurações | Configurações de férias com código e descrição, atribuídas a grupos numa lista ordenada (Férias e Férias - Simulação). A ordem define a prioridade quando o colaborador participa de mais de um grupo. Etapas de aprovação por grupo nos demais processos |
| Grupos de colaboradores | Grupo principal do cadastro mais participações adicionais. Atribuições de Seleção também seguem a ordem da lista |
| Informações adicionais, redirecionamento e prazo de aprovação | Campos extras configuráveis por processo (texto, número, data, sim/não, lista), gravados na solicitação e exportados pelo Migrador; etapa que finaliza o processo (redirecionamento) sem executar as seguintes; prazo mínimo para aprovação de férias (dias antes do início) |
| Guia Férias (Configurações Gerais) | Configuração por grupo de cálculo e, opcionalmente, por tipo de cargo: folha de férias normais, com abono, com 13º e com abono e 13º; folha da 1ª, 2ª e 3ª ocorrência no mês (e as de simulação); tratamento para valores mensais (nenhum, bloquear ou excluir). Grupos de cálculo e folhas ficam em Folha de Pagamento; o tipo de cargo, em Configurações por Empresa › Outros |
| Workflow e SLA | Até 5 etapas por processo (gestor imediato, RH, administrador, RH de destino), prazo em horas úteis com expediente e calendário, alerta de atraso |
| Gerenciador de Processos | Fila de aprovação, histórico de cada ação, relatório de SLA, notificações na tela e por e-mail opcional (SMTP) |
| Migrador / Ambientes | Homologação e produção separadas, exportação e importação JSON com prévia, publicação aprovada pelo administrador. Todos os novos parâmetros seguem esse fluxo |
| Organograma | Árvore pela hierarquia de gestor imediato |
| Recrutar e Selecionar | Requisição aprovada abre a vaga, banco de talentos com consentimento (LGPD) e anonimização, etapas e critérios parametrizáveis, avaliações com nota, entrevistas com controle de agenda, vagas internas no autoatendimento e contratação integrada: candidato externo vira admissão e depois funcionário, interno vira movimentação |

Ficam fora do escopo: avaliação de desempenho, SST, People Analytics, assinatura digital com certificado e integração com o eSocial.

## Navegação

O sistema tem duas experiências:

- **Suíte (RH, consultor e administrador):** menu lateral em três grupos. *Operação* traz Painel, Funcionários, Gerenciador de processos, Admissão, Férias, Ponto, Folha, Benefícios e Recrutamento. *Configuração* traz Suíte de parametrização, Empresas, Cargos e vagas, Processos parametrizáveis, Ambientes/Migrador e Usuários e acessos. *Treinamento* traz Aprenda a Parametrizar.
- **Suíte de parametrização:** árvore de configurações organizada por caminhos. Cada tela mostra o caminho completo, a barra de ações (Salvar, Recarregar, Ajuda) e a explicação do parâmetro. Exemplos:
  - *Configuração › Configurações por Empresa › Contratação / Férias e Recesso / Rescisão e Aviso Prévio / Outros (Vale Transporte, Posição, Quadro de Vagas, Tabela Salarial)*;
  - *Configuração › Autoatendimento › Processos › Atribuições de Seleção de Colaboradores / Atribuições de Configurações / Férias / Configurações Gerais (Guias SLA, Gerenciador de Processos, Férias, Benefícios, Dependentes, Alteração de Dados Pessoais, Afastamento/Ausência, Movimentação, Requisição, Rescisão, Recesso, Vale Transporte, Lançamento de Valores, Servidor de E-mail, Migrador)*;
  - *Configuração › Ponto / Folha de Pagamento / Recrutamento e Seleção / Ambientes e Publicação / Segurança*.
- **Portal do Colaborador (perfil Funcionário):** sem menu lateral. O botão ≡ abre Início, Meus Dados, Solicitações, Recrutamento (vagas internas), Minhas Tarefas (gestores), Gerenciar Processos, Férias, Controle de ponto e Benefícios.

## Perfis e o que cada um vê

| Perfil | Opera | Parametriza | Vê |
| --- | --- | --- | --- |
| Administrador do Sistema | Aprova publicações, decide etapas quando for o aprovador | Sim, em homologação (todas as empresas) | Tudo, inclusive Usuários e acessos |
| Consultor Funcional | Só simula (férias, processos, ponto, folha); não abre solicitações nem registra ponto | Sim, em homologação (todas as empresas); solicita publicação | Suíte completa, Treinamento, Migrador |
| Gestor de RH | Abre e aprova solicitações da própria empresa, mantém cadastros (funcionários, tabela salarial, quadro de vagas, posições, vale-transporte, grupos) | Não; consulta a Suíte em modo leitura | Só a própria empresa; sempre em Produção |
| Funcionário (Portal) | Solicita para si (e para a equipe, conforme Atribuições de Seleção), aprova como gestor imediato, registra ponto | Não; não lê parametrização (só regras de ponto e benefícios) | Só os próprios dados e os da equipe |

Grupos de acesso (Usuários e acessos) restringem ainda mais os módulos de um Funcionário ou Gestor de RH: férias, ponto, benefícios, admissão, folha, processos e recrutamento.

## Como experimentar

1. Entre como administrador e crie contas: um Gestor de RH para a ABC e uma conta de Funcionário para João Silva, que é gestor imediato dos demais colaboradores da ABC.
2. Abra **Processos e aprovações → Parametrização**, em Homologação. Ajuste os fluxos (por exemplo, Afastamento: gestor imediato e depois RH), o SLA e as atribuições de seleção. Publique em **Ambientes**.
3. Com a conta de um colaborador, abra **Processos → Nova solicitação** e envie uma solicitação, como um afastamento. O gestor imediato e depois o RH aprovam no **Gerenciador de processos**.
4. Em **Cargos, vagas e organograma**, defina o quadro de vagas e a tabela salarial. Em seguida, faça uma requisição de pessoal e aprove-a.
5. Em **Recrutamento e seleção**, a vaga aparece em rascunho. Complete a descrição e os requisitos e publique. Inclua candidatos do banco de talentos, conduza as etapas e avaliações, agende entrevistas e, na última etapa, clique em **Contratar**. Aprove a admissão gerada e confira o novo funcionário. Colaboradores veem as vagas internas e se candidatam pelo autoatendimento.

## Operações

```powershell
docker compose logs -f backend
docker compose exec backend pytest -q -p no:cacheprovider
docker compose exec backend alembic upgrade head
docker compose exec backend python -m app.bootstrap_admin --email admin@empresa.com.br
```

Recriar os dados demonstrativos (preserva os administradores e remove as demais contas e registros):

```powershell
docker compose exec -e RESET_DEMO=YES backend python -m app.reset_demo
```

No Windows, o servidor de desenvolvimento do frontend pode não perceber alterações de arquivos. Nesse caso, rode `docker compose restart frontend`.

## Integração com o ERP-AI

O projeto `C:\projects\ERP-AI` usa `/api/integration/v1`. A integração fica desligada enquanto `HCM_INTEGRATION_TOKEN_SHA256` estiver vazio no `.env`.

- **Escopo:** o piloto cobre ABC (`EMP001`) e os módulos de férias e Fase 4, com consulta, validação e aplicação somente em homologação.
- **Aplicação:** exige também `X-HCM-Execution-Key`, verificado por `HCM_EXECUTION_TOKEN_SHA256`. As operações ficam em `integration_operations` e podem ser revertidas.
- **Revogar o acesso:** apague as duas variáveis do `.env` e reinicie o backend.

## Estrutura

- `frontend/`: Next.js, React e TypeScript.
  - `ProcessCenter.tsx`: processos, gerenciador e parametrização.
  - `CompanySetup.tsx`: cargos, vagas e organograma.
  - `EmployeeDetail.tsx`: ficha do funcionário.
  - `RecruitmentCenter.tsx`: recrutamento e seleção.
- `backend/app/`: FastAPI e SQLAlchemy.
  - `processes.py`: motor de workflow, SLA e atribuições.
  - `policies.py`: parâmetros versionados.
  - `setup_api.py`: dados mestres da empresa.
  - `recruitment_api.py`: vagas, candidatos, etapas, entrevistas e contratação.
  - `security.py`: sessões, senhas e bloqueio.
- `backend/migrations/`: histórico Alembic. Depois da 0001, as alterações usam `app/migration_utils.py`, que é idempotente e permite instalar do zero.
- `compose.yaml`: frontend, backend e PostgreSQL com volume persistente.
