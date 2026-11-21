# Proposta: empacotar o Agent Bar como app Windows (P1, só proposta)

> S00-13 · Frente C · 2026-11-20. **Nada aqui foi implementado.** O pedido é não empacotar antes de a interface estabilizar.
> Hoje o app abre pelo atalho "Plan2Space Agent Bar" (`launchers/install_shortcut.ps1`), que roda o `pythonw.exe` do `.venv` do checkout.
> As comparações abaixo vêm do conhecimento geral das ferramentas. Antes de escolher, conferir versão e licença atuais de cada uma num spike.

## O que o pacote precisa resolver

| Requisito | Hoje (atalho + venv) |
| --- | --- |
| Rodar sem Python global | ok: usa o CPython do `.venv` (o global está quebrado nesta máquina) |
| Instalador | não há: `py -m venv` + `pip install -e .[gui]` + `install_shortcut.ps1` |
| Versionamento visível | só o commit do checkout |
| Logs | `%LOCALAPPDATA%\agent_bar\logs\agent_bar.log` (rotação 1 MB × 3) |
| Configuração externa | `launchers\local.env` (fora do git) + `%APPDATA%\agent_bar\settings.json` |
| Atualização | `git pull` no checkout; o atalho continua valendo |

## Alternativas

| Ferramenta | Como funciona | Prós | Contras |
| --- | --- | --- | --- |
| **PyInstaller** (`--onedir`, `--windowed`) | congela o interpretador e as dependências numa pasta com `.exe` | é a mais usada com PySide6 (hooks prontos para Qt); dá um `.exe` sem console; build rápido | pasta grande (os plugins do Qt dominam); antivírus às vezes flaga `.exe` congelado sem assinatura; o `--onefile` extrai para `%TEMP%` a cada abertura (lento, pior com Defender) |
| **Nuitka** (`--standalone`, plugin `pyside6`) | compila o Python para C e liga com o runtime | inicia mais rápido; código menos legível; plugin próprio para PySide6 | build bem mais lento e precisa de compilador C (MSVC); mais pontos de falha no CI |
| **Briefcase** (BeeWare) | gera app e instalador MSI a partir do `pyproject` | instalador MSI e versionamento saem do projeto; fluxo de update definido | é feito em torno do Toga; com PySide6 funciona, mas com menos exemplos; acrescenta convenções de layout |
| **cx_Freeze** | congela como o PyInstaller; tem alvo `bdist_msi` | gera MSI direto | comunidade e hooks de Qt menores que os do PyInstaller |
| **Manter venv + atalho** (estado atual) | — | zero build; atualização = `git pull`; o log já existe | depende de `py`/`uv` para criar o venv; não é "um clique" numa máquina nova |

## Recomendação

1. **Agora:** manter venv + atalho até a interface estabilizar (pedido do Felipe). Já está entregue: instância única, log em arquivo, caixa de erro antes da UI e ícone próprio.
2. **Quando estabilizar:** **PyInstaller `--onedir --windowed`**, com o `.ico` do repo e a versão lida do `pyproject` (mostrada no título e no log), gerado no CI `windows-latest` como artefato do release. `--onedir`, e não `--onefile`, para não extrair a cada abertura.
3. **Instalador:** um ZIP versionado mais `install_shortcut.ps1` adaptado ao `.exe` resolve o caso de uma máquina. MSI (Briefcase/cx_Freeze/WiX) só se houver mais usuários.
4. **Configuração e logs ficam fora do pacote:** `local.env` passa para `%APPDATA%\agent_bar\local.env`, e o log continua em `%LOCALAPPDATA%`. Assim, atualizar o pacote não apaga a configuração.
5. **Atualização:** trocar a pasta `onedir` pela nova versão; o atalho aponta para um caminho estável (`%LOCALAPPDATA%\Programs\Plan2Space Agent Bar\`). Sem auto-update no começo.

## Perguntas para o Felipe

- Iniciar com o Windows **não** foi feito e **não** está proposto. Você quer isso? Se sim, por atalho na pasta Inicializar do usuário (sem serviço)?
- Assinatura de código: vale o custo de um certificado para o Defender e o SmartScreen não reclamarem do `.exe`?
- O pacote fica só nesta máquina, ou vai para outra pessoa? Isso decide entre ZIP + script e MSI.
