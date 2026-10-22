# Central_Wiki

Aplicação web inicial para organizar a documentação e os procedimentos da empresa. A versão atual inclui contas internas, setores, funções por setor, artigos com rascunho/revisão/publicação, histórico de versões, busca, API REST inicial e auditoria.

## Instalação e inicialização no Windows

Requisitos: Windows PowerShell e conexão com a internet na primeira instalação. O launcher instala Python 3.14 pelo repositório WinGet da Microsoft se não encontrar Python 3.10 ou superior; as bibliotecas são instaladas do índice oficial do PyPI.

1. Dê duplo clique em `Central_Wiki.cmd` ou abra o atalho com o nome atual do aplicativo na Área de Trabalho. Para iniciar pelo PowerShell, execute:

   ```powershell
   .\Central_Wiki.cmd
   ```

Em cada execução, o launcher verifica se os arquivos essenciais e os arquivos de templates/estilos estão presentes e não vazios, instala/repara as dependências declaradas e valida imports e compatibilidade com `pip check`. Se o banco já existir, executa `PRAGMA quick_check` e `foreign_key_check`; caso detecte corrupção, para e orienta restaurar um backup, sem apagar ou sobrescrever os dados. O Python e as bibliotecas vêm de fontes oficiais via HTTPS: WinGet/Microsoft e PyPI. Arquivos do próprio projeto ausentes ou vazios são reportados para restauração de uma cópia confiável; o launcher não baixa código do projeto de uma origem não configurada. O launcher cria o ambiente virtual `.venv`, solicita os dados do primeiro administrador quando necessário, escolhe um IPv4 ativo e inicia o servidor na porta `8443` (ou na porta configurada), abrindo a página no navegador. Também cria ou atualiza um atalho permanente na Área de Trabalho com o nome configurado para o aplicativo; a janela do terminal usa esse nome seguido de **Launcher**. Ao mudar o nome/logo no painel de TI, o atalho e o terminal são atualizados quando o launcher atualizar o menu ou for iniciado novamente. Não é necessário ativar o ambiente virtual manualmente. A senha do primeiro administrador não é exibida; nas execuções seguintes, a conta existente é mantida.

Para acesso por outros dispositivos **na mesma rede local**, use o endereço exibido pelo launcher (por exemplo, `http://192.168.3.15:8443`). A porta `8443` não habilita HTTPS automaticamente: o aplicativo ainda usa HTTP sem um proxy reverso configurado com TLS. A inicialização não altera o Firewall do Windows. Para habilitar o acesso remoto, escolha **Verificar e ativar acesso remoto na rede local** no menu (opção 7); o launcher mostra o pedido e solicita autorização do Windows (UAC), sem abrir um segundo terminal, para criar ou corrigir uma regra persistente. Aprove a solicitação do UAC; a regra permite apenas TCP na porta configurada e conexões da sub-rede local. Se a autorização for recusada ou a criação falhar, o launcher informa o motivo e salva os detalhes em `data/firewall-setup.log`. O servidor continua funcionando neste PC, mas outros dispositivos podem não conseguir conectar. Essa configuração não encaminha portas no roteador nem libera acesso pela internet. O endereço IP pode mudar quando a rede ou o roteador for reiniciado. Redes Wi-Fi de convidados e redes com isolamento de dispositivos podem bloquear a comunicação mesmo com o firewall configurado.

Mantenha a janela do launcher aberta para controlar o servidor. Use o menu para iniciar, parar, reiniciar ou abrir o site, escolha **Alterar porta do servidor** para definir uma porta persistente em `data\launcher-settings.json` (o servidor ativo será reiniciado), ou escolha **Alternar tema claro/escuro** para trocar o tema do terminal. A porta precisa estar disponível; se estiver ocupada, o launcher informa o problema em vez de selecionar outra automaticamente. **Sair** encerra o servidor. O launcher registra PID/endereço em `data\server.json`; registros de execução ficam em `data\app.log` e `data\app-error.log`.

O aplicativo oferece tema claro/escuro com preferência salva no navegador; quando ainda não há preferência, acompanha a configuração de aparência do sistema. O Bootstrap 5 é carregado do CDN do jsDelivr com versão fixada e integridade SRI; é necessária conexão com a internet para baixar seu CSS no navegador. O estilo específico da aplicação continua em `static\style.css`.

Depois de entrar, crie os setores e cadastre usuários. Usuários não podem se registrar por conta própria e podem alterar a própria senha pela opção de conta no rodapé do menu.

Para instalar também as ferramentas de teste e executar a suíte:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest
```

## Configuração

- `WIKI_HOST`: endereço de escuta; padrão `127.0.0.1`.
- `WIKI_PORT`: porta HTTP; padrão `8443`. No terminal, defina essa variável antes de executar `.\Central_Wiki.cmd` ou `python main.py` para substituir a porta configurada no launcher.
- `WIKI_DB_PATH`: caminho opcional do banco SQLite; padrão `data/wiki.db`.
- `WIKI_SECRET_KEY`: segredo opcional para assinatura de sessões. Se não for definido, uma chave local é criada em `data/.secret`.
- `WIKI_COOKIE_SECURE=1`: habilita cookies `Secure` quando a aplicação está atrás de HTTPS.

O launcher `start-local.ps1` escolhe automaticamente um IPv4 da máquina e usa a porta `8443` por padrão; a opção do menu **Alterar porta do servidor** salva uma nova porta, e `WIKI_PORT` no terminal tem prioridade sobre essa configuração. O início direto com `python main.py` também usa `8443` por padrão e aceita `WIKI_PORT`. Para substituir a porta pelo PowerShell nesta sessão:

```powershell
$env:WIKI_PORT = "8444"
.\Central_Wiki.cmd
```

O início direto com `python main.py` escuta somente em `127.0.0.1` por padrão; para uso na rede local, prefira o launcher, que seleciona o IPv4 da máquina. O acesso pela internet exige uma solução segura com HTTPS e VPN ou proxy autenticado; não exponha diretamente este servidor HTTP nem encaminhe a porta no roteador. Os dados locais ficam na pasta `data/`, excluída do controle de versão. Faça backup da pasta regularmente. Para uso externo ou produção, configure HTTPS, acesso restrito/VPN, gestão de segredos, política de backup e revisão técnica antes de expor o servidor.

## Funções e publicação

- **Leitor:** consulta artigos publicados do(s) setor(es) vinculado(s).
- **Editor:** cria e altera artigos dos seus setores e pode enviar propostas de conteúdo para análise.
- **Moderador:** é vinculado a setores específicos; edita e revisa propostas nesses setores, podendo integrar as escolhidas ao artigo.
- **Administrador de TI:** gerencia usuários/setores, acessa todos os setores, integra propostas e configura a identidade do aplicativo.

Ao criar uma conta, a administração informa o nome de usuário usado no login, nome de exibição, senha inicial e, opcionalmente, um setor existente e a função nesse setor. Contas podem receber vínculos adicionais depois. O Editor atua somente nos setores vinculados; Moderador também fica limitado aos setores em que recebeu essa função, sem ganhar acesso global.

As propostas de edição ficam numa fila por artigo. Um Editor envia uma sugestão sem substituir o conteúdo oficial; um Moderador daquele setor ou Administrador de TI pode integrar ou rejeitar cada proposta, individualmente. A integração é registrada no histórico de versões; se o artigo já estava publicado, a nova versão volta para revisão antes de ser publicada novamente. A edição direta também verifica a versão carregada e recusa salvar sobre uma alteração mais recente, orientando a recarregar ou enviar uma proposta. Isso não é coedição em tempo real: propostas e decisões são salvas no servidor.

Na tela de administração, o administrador pode iniciar **Visualizar como usuário comum**, escolher um setor e navegar como leitor: só verá artigos publicados daquele setor. O modo é somente leitura, identificado por um aviso no topo; para voltar à conta administrativa, clique em **Encerrar visualização**. A conta e a sessão do administrador são mantidas.

Nomes de usuário devem ter entre 3 e 80 caracteres; nomes de setor podem ter até 80 caracteres. Senhas têm mínimo de 12 caracteres. Alterar senha ou desativar uma conta invalida as sessões anteriores.

O nome e sobrenome de exibição são cadastrados pela administração e não podem ser alterados pelo próprio usuário; **Minha conta** permite trocar a senha e, opcionalmente, enviar/remover a própria foto de perfil (PNG, JPEG ou WebP, até 2 MB; SVG não é aceito). A imagem fica no banco e só é entregue à conta autenticada. A barra lateral exibe nome, foto e função/setor; para contas antigas sem nome cadastrado, exibe o nome de usuário.

O painel **Personalizar aplicativo**, visível apenas para Administradores de TI, permite configurar nome, logo, nomes das páginas principais e rótulos dos botões de conteúdo. Aceita texto simples e logo PNG/JPEG/WebP até 2 MB; não executa HTML ou JavaScript fornecido pelo usuário.

O logo ocupa uma área reservada de 64 × 64 px na marca do menu e aparece também na tela de login. Quando configurado, é usado como ícone da aba do navegador (favicon); todas as páginas mantêm somente o nome configurado do aplicativo no título da aba. No Windows, o launcher também tenta converter o logo para o ícone do atalho; se o formato não puder ser convertido pelo sistema, o atalho mantém o ícone padrão do Windows.

O acesso é validado no servidor. A interface inicial não oferece cadastro aberto, integrações externas ou treinamento com avaliações; esses módulos podem ser desenvolvidos em etapas posteriores.

### Conteúdo modular e vídeos

O editor de artigos permite organizar módulos de texto, títulos, imagens por endereço HTTPS público e vídeos do YouTube. Os links padrão de vídeo, `youtu.be`, Shorts e links de incorporação são reconhecidos; os vídeos são exibidos usando `youtube-nocookie.com`, sem necessidade de chave de API, com limite de 10 vídeos por artigo. O artigo pode ter até 100 módulos.

Por segurança, HTML e JavaScript fornecidos nos artigos não são executados; o conteúdo é validado e renderizado como módulos conhecidos. Imagens e vídeos são carregados diretamente pelo navegador a partir dos provedores externos, que podem receber dados de conexão do leitor. Sem JavaScript, é possível editar texto simples; para criar, organizar ou preservar os módulos de mídia, mantenha JavaScript habilitado.

## API inicial

- `GET /api/v1/sectors`
- `GET /api/v1/articles?q=termo&limit=100&offset=0` (limite entre 1 e 100)

As APIs usam a sessão autenticada no navegador e respeitam os setores autorizados. A listagem web exibe até 50 artigos por página; a API permite navegar pelos resultados com `limit` e `offset`.

## Testar o Wiki.js localmente

O [Wiki.js](https://js.wiki/) pode ser iniciado em paralelo à aplicação acima para avaliação no navegador. Esse teste usa Docker Compose, PostgreSQL e a porta `3000`; não altera nem substitui a aplicação Python, que continua na porta `8443` por padrão.

1. Instale e inicie o Docker Desktop.
2. No PowerShell, abra esta pasta e ative o teste:

   ```powershell
   powershell -ExecutionPolicy Bypass -File .\start-wikijs-test.ps1
   ```

3. O script escolhe o IPv4 da interface de rede ativa e uma porta alta disponível, inicia os serviços e abre o navegador quando o Wiki.js responder. Siga o assistente inicial para criar a conta administrativa.
4. Para parar os serviços, execute nesta pasta:

   ```powershell
   docker compose down
   ```

O conteúdo do Wiki.js fica em um volume Docker e é preservado ao parar os serviços. A senha padrão do banco no Compose é apenas para teste local; para alterá-la, defina `WIKIJS_DB_PASSWORD` no ambiente antes de iniciar o Compose. A interface é vinculada ao IPv4 escolhido e pode ser acessível por outros dispositivos da rede; o firewall do Windows pode precisar permitir essa conexão. O teste Wiki.js é opcional e requer Docker Desktop além do Python necessário para a aplicação principal.
