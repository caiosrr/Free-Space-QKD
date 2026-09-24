# 06. Segurança do mount

Conferido contra o commit `332b9e1`, em 2026-09-23.

Camada 2 da documentação: lê o código de verdade. Para o mapa geral de uma
sessão, leia antes o `01_caminho_de_uma_sessao.md`. Este documento cobre tudo
que existe para **parar o mount quando algo dá errado**, da proteção mais
próxima do laço de controle até a que roda depois de o PC reiniciar.

Cada seção termina com uma marca de revisão, que só você altera, e com
perguntas **para conferir**: se você consegue respondê-las sem voltar ao texto,
a seção foi entendida.

---

## 0. A premissa de que tudo depende

Existe um único fato que justifica este documento inteiro:

> **O `MoveAxis` do ASCOM não tem prazo.** Uma vez comandado, o eixo anda até
> alguém mandar velocidade zero. Não há timeout no firmware, e perder a conexão
> não para nada.

Não é suposição. Foi medido neste mount em 2026-09-14 (roteiro, seção "O
MoveAxis não para sozinho"):

| teste | resultado |
|---|---|
| eixo comandado e nada mais enviado | andou em linha reta até o fim: 102,0″ contra 101,7″ previstos |
| servidor ASCOM **fechado** com o eixo andando | andou 266″ contra 246″ previstos para a janela inteira |
| cabo USB **arrancado** com o eixo andando (2026-09-16) | não para |

E a documentação do ASCOM confirma: o `MoveAxis` faz o telescópio mover "e
continuar indefinidamente". O defeito não é do ZWO; é o nosso uso que é
incomum. Astrofotografia usa `MoveAxis` para movimentos curtos com alguém
olhando. Nós o usamos em sessões de 10 horas sem ninguém.

Consequência: **toda proteção deste documento se resume a garantir que alguém
mande o zero**, mesmo quando quem deveria mandá-lo morreu.

Todas elas terminam, de um jeito ou de outro, nesta função:

`Codigos/modulos/controle/mount_ascom.py`, linhas 75 a 97

```python
def stop_axes_safely(attempts: int = 2, timeout: float = 2.0) -> bool:
    """Tenta zerar cada eixo de forma independente, mesmo se o outro falhar."""
    failed_axes = []
    for axis in (0, 1):
        stopped = False
        for _ in range(max(1, int(attempts))):
            try:
                call(
                    "PUT",
                    "moveaxis",
                    data={"Axis": axis, "Rate": 0.0},
                    timeout=timeout,
                )
                stopped = True
                break
            except Exception:
                continue
        if not stopped:
            failed_axes.append(axis)
    if failed_axes:
        print(f"ALERTA: nao consegui confirmar parada dos eixos {failed_axes}.")
        return False
    return True
```

Dois detalhes que importam:

- **Os eixos são independentes.** Se o azimute falhar, a altitude ainda é
  tentada. Um laço que parasse no primeiro erro deixaria o segundo eixo solto.
- **"Confirmar" aqui quer dizer que o servidor aceitou o comando**, não que o
  eixo parou de fato. Essa distinção volta na seção 7, onde a parada pela
  serial confere pela posição.

> Revisado por Caio: ainda não

**Para conferir**

1. Se o tracker morresse no meio de um micropulso e nada mais acontecesse,
   quanto o mount andaria em 1 hora? (Velocidade do micropulso: 0,001042 °/s.)
2. Por que o teste com o servidor fechado mostrou 266″ e não os 246″ previstos?
   Isso é problema ou é esperado?
3. `stop_axes_safely` devolve `True`. Isso garante que o mount parou?

---

## 1. O mapa das proteções

Nove mecanismos, do mais próximo do laço ao mais distante. Cada um cobre um
cenário que o anterior não alcança.

| # | proteção | onde roda | cobre | não cobre |
|---|---|---|---|---|
| 2 | laço de controle | thread do tracker | medida velha ou ausente | tracker morto |
| 3 | vigia interno da sessão | thread do tracker | tempo, deslocamento, mount mudo | tracker morto |
| 4 | teto de velocidade | configuração | limita o estrago de qualquer falha | nada por si só |
| 5 | aviso de desligamento | handler do Windows | janela fechada no X | **reinício do Windows Update** |
| 6 | vigia externo | processo filho | tracker morto, Windows de pé | PC inteiro morto |
| 7 | escada de parada | dentro do vigia | servidor ASCOM morto ou travado | PC inteiro morto |
| 8 | trava de uso | todos os que param | parar um uso legítimo por engano | nada, é o contrário |
| 9 | tarefas de boot | agendador do Windows | PC reiniciou | PC que não volta |

As seções seguem essa ordem.

---

## 2. Dentro do laço de controle

A primeira proteção é o próprio laço não mandar nada quando não sabe onde o
beacon está.

`Codigos/modulos/controle/tracker_loop.py`, linha 67

```python
SIGNAL_TIMEOUT_S = 0.45
```

`Codigos/modulos/controle/tracker_loop.py`, linhas 378 a 388

```python
                measurement_age = (loop_t0 - measurement_ts) if measurement_ts else 1e9
                signal_ok = has_signal and (measurement_age <= SIGNAL_TIMEOUT_S)

                if not signal_ok:
                    err_az = err_alt = 0.0
                    target_cmd_az = target_cmd_alt = 0.0
                    prev_radius_px = None
                    prev_dx_filt_px = None
                    prev_dy_filt_px = None
                    runaway_count = 0
                    repousar("sem_sinal")
```

Se a última medida tem mais de 0,45 s, o comando alvo vira zero. O laço de
controle **nunca espera a câmera**: ele roda a ~34 Hz com a medida que houver,
e uma medida velha é tratada como ausência de medida.

E, aconteça o que acontecer dentro do laço, a saída dele manda zero:

`Codigos/modulos/controle/tracker_loop.py`, linhas 685 a 686

```python
        finally:
            stop_axes_safely()
```

O `finally` do Python roda na saída normal, numa exceção e no Ctrl+C. **Não
roda** se o processo for morto de fora. Esse é o limite de tudo que mora dentro
do tracker, e o motivo de existirem as seções 5 a 9.

> Revisado por Caio: ainda não

**Para conferir**

1. A câmera trava por 2 segundos e depois volta. O que o laço de controle manda
   ao mount durante esses 2 segundos?
2. Cite dois jeitos de o tracker terminar em que este `finally` roda, e um em
   que não roda.

---

## 3. O vigia interno da sessão

Uma thread separada, a 5 Hz, lê a posição do mount e encerra a sessão em três
situações.

`Codigos/modulos/configuracoes/tracker.py`, linhas 306 a 311

```python
# Limites da sessao longa.
MAX_SESSION_HOURS = 2.0
MAX_OFFSET_AZ_DEG = 5.0
MAX_OFFSET_ALT_DEG = 5.0
POSITION_WATCHDOG_HZ = 5.0
WATCHDOG_READ_FAILURES = 5
```

`Codigos/modulos/controle/tracker_seguranca.py`, linhas 59 a 67

```python
    while True:
        loop_started = time.perf_counter()
        with state.lock:
            if state.stop:
                return

        if (loop_started - session_started) >= max_session_seconds:
            solicitar_parada(state, "tempo_maximo_da_sessao")
            return
```

`Codigos/modulos/controle/tracker_seguranca.py`, linhas 85 a 103

```python
            if abs(offset_az) >= MAX_OFFSET_AZ_DEG:
                solicitar_parada(
                    state,
                    f"limite_absoluto_az_{offset_az:+.4f}_deg",
                )
                return
            if abs(offset_alt) >= MAX_OFFSET_ALT_DEG:
                solicitar_parada(
                    state,
                    f"limite_absoluto_alt_{offset_alt:+.4f}_deg",
                )
                return
        except Exception as exc:
            consecutive_failures += 1
            with state.lock:
                state.watchdog_error = str(exc)
            if consecutive_failures >= WATCHDOG_READ_FAILURES:
                solicitar_parada(state, "watchdog_mount_sem_resposta")
                return
```

As três situações:

| gatilho | motivo registrado |
|---|---|
| a sessão atingiu o tempo pedido | `tempo_maximo_da_sessao` |
| o mount se afastou 5° do início, em qualquer eixo | `limite_absoluto_az_...` ou `..._alt_...` |
| 5 leituras de posição seguidas falharam, ou seja, 1 s de mount mudo | `watchdog_mount_sem_resposta` |

### O fim pelo tempo também é uma parada de segurança

Este é o ponto mais fácil de entender errado, e eu mesmo o entendi errado numa
conversa: **não existe um "fim normal" pelo tempo.** O único código que encerra
a sessão quando o tempo acaba é esta thread, e ela registra
`tempo_maximo_da_sessao` como **motivo de segurança**. Isso tem uma
consequência direta no `finally` da sessão:

`Codigos/modulos/controle/tracker_sessao.py`, linhas 344 a 360

```python
        stop_axes_safely()

        if (
            safety_reason
            and initial_position is not None
            and RETURN_TO_START_ON_LIMIT
            and motivo_permite_retorno(safety_reason)
        ):
            print(f"Retornando a posicao inicial (motivo: {safety_reason}).")
            return_result = retornar_posicao_inicial(*initial_position)
            print(
                "Posicao inicial restaurada."
                if return_result.get("success")
                else f"ALERTA: retorno nao confirmado: {return_result}"
            )
        elif safety_reason == "sinal_perdido_por_tempo_excessivo":
            print("Sinal ausente: mount parado, sem busca ou retorno cego.")
```

Ou seja: **quando o tempo acaba, o mount volta à posição em que a sessão
começou.** Confirmado nos dados: o `resumo.json` da sessão de 10 h de
2026-09-15 registra `finish_reason = tempo_maximo_da_sessao` e
`return_to_start.success = True`.

O retorno só é negado num caso:

`Codigos/modulos/controle/tracker_seguranca.py`, linhas 31 a 33

```python
def motivo_permite_retorno(reason: str | None) -> bool:
    """Nao inicia movimento cego quando o beacon desapareceu por completo."""
    return reason != "sinal_perdido_por_tempo_excessivo"
```

Se o beacon sumiu por 10 minutos, o sistema não sabe mais onde nada está, e se
mover às cegas seria pior que ficar parado.

O retorno em si é cauteloso: no máximo 0,2 °/s, duas tentativas, tolerância de
0,0005°, e recusa qualquer deslocamento maior que o limite de 5° mais 0,5° de
margem (`tracker_seguranca.py`, linhas 133 a 139).

> Revisado por Caio: ainda não

**Para conferir**

1. Uma sessão de 3 h termina às 23h. Onde o mount estará apontando às 23h01:
   onde o tracker o deixou às 22h59, ou onde ele estava às 20h?
2. Você quer centralizar o feixe com o tracker e depois observar sem corrigir.
   Por que rodar o tracker por 15 minutos e depois o observador **não** resolve?
3. Por que a leitura de posição tolera 4 falhas seguidas e não para na
   primeira?
4. O limite de 5° protege do tracker se perder numa correção absurda. Ele
   protege do mount andar sozinho depois que o tracker morreu?

---

## 4. O teto de velocidade

Não é uma proteção que age; é a que limita o estrago quando todas as outras
falham.

`Codigos/modulos/configuracoes/tracker.py`, linhas 313 a 328

```python
# Velocidade maxima durante o tracking. O limite menor reduz a distancia que o
# mount pode percorrer entre duas verificacoes do watchdog.
#
# Baixado de 0,10 para 0,005 em 2026-09-16, e o motivo e o caso em que o tracker
# MORRE. Enquanto ele vive, o watchdog de posicao ja limita o percurso; quando
# ele morre, o watchdog morre junto e o unico limite que resta e a velocidade do
# comando que ficou em curso, porque o MoveAxis nao tem prazo.
#
# Nao ha custo: todo comando do laco passa pelo BoundedCorrectionCycle, que
# forca a velocidade minima de 0,001042 deg/s. Medido nas duas sessoes de
# 2026-09-15, em 172 comandos nao nulos, a taxa foi SEMPRE 0,001042 e nenhuma
# outra. O teto de 0,10 nunca chegou a ser exercido.
#
# O que muda e o pior caso de deriva: de 360 graus/h para 18 graus/h, vinte
# vezes menos. O autoteste e o retorno tem tetos proprios e nao sao afetados.
MAX_TRACKING_RATE_DEG_S = 0.005
```

Os números, para ter de cabeça:

| velocidade | por segundo | por hora |
|---|---|---|
| micropulso, a que o laço usa sempre | 3,75″ | 3,75° |
| teto do tracker | 18″ | 18° |
| teto antigo, antes de 2026-09-16 | 360″ | 360° |

> Revisado por Caio: ainda não

**Para conferir**

1. O comentário diz que baixar o teto "não tem custo". Qual medida sustenta
   isso?
2. O retorno à posição inicial anda a 0,2 °/s, bem acima do teto de 0,005.
   Por que isso não contradiz o teto?

---

## 5. O aviso de desligamento do Windows

O Windows avisa programas de console antes de fechá-los, mas o Python ignora
esses avisos: o processo morre sem rodar nenhum `finally`. O tracker registra
um handler próprio para aproveitar o aviso e mandar o zero.

`Codigos/modulos/controle/tracker_sessao.py`, linhas 150 a 152

```python
        # attempts/timeout, entao o lambda descarta o argumento.
        if desligamento_windows.registrar(lambda _evento: stop_axes_safely()):
            print("Parada de emergencia armada para o desligamento do Windows.")
```

`Codigos/modulos/controle/desligamento_windows.py`, linhas 70 a 83

```python
    def _handler(evento: int) -> bool:
        if evento in (CTRL_CLOSE_EVENT, CTRL_LOGOFF_EVENT, CTRL_SHUTDOWN_EVENT):
            nome = NOMES.get(evento, str(evento))
            print(f"\n{nome} detectado: parando o mount antes de sair.", flush=True)
            # Num thread separado com prazo: se o ASCOM travar, o processo ainda
            # e morto pelo Windows, e ficar preso aqui nao ajudaria em nada.
            t = threading.Thread(target=parada_de_emergencia, args=(nome,), daemon=True)
            t.start()
            t.join(timeout=4.0)
            print("mount parado." if not t.is_alive() else "tempo esgotado.", flush=True)
            # False deixa o Windows seguir com o encerramento, que e o certo:
            # ja fizemos o que dava, e segurar o desligamento irrita o operador.
            return False
        return False
```

**O limite medido, que é o mais importante desta seção** (roteiro, "O handler
de desligamento não dispara no reinício", 2026-09-14):

| evento | o handler disparou? |
|---|---|
| janela do console fechada no X | sim |
| reinício do Windows Update | **não** |

O mecanismo funciona; o Windows é que não avisa o processo num reinício. Esta
proteção vale bem menos do que parecia quando foi escrita. Contra reinício, a
defesa real é a tarefa de boot da seção 9.

Uma armadilha de método registrada no roteiro: fechar a janela e reiniciar
chegam pelo **mesmo** handler. Um teste que só anota "fui chamado" aprovaria o
reinício depois de um simples fechar de janela. Por isso o handler recebe o
**nome** do evento.

> Revisado por Caio: ainda não

**Para conferir**

1. Por que a parada roda numa thread com prazo de 4 s, em vez de ser chamada
   direto dentro do handler?
2. Você fecha sem querer a janela do tracker no X. O mount para?
3. E se o Windows Update reiniciar o PC às 3h?

---

## 6. O vigia externo

Tudo das seções 2, 3 e 5 mora **dentro** do processo do tracker e morre junto
com ele. O vigia externo é outro processo, que só observa se a telemetria
continua sendo escrita.

O tracker o sobe sozinho, como processo filho:

`Codigos/programas_principais/tracker.py`, linhas 23 a 27

```python
# Folga entre a ultima linha de telemetria e o disparo. Os 25 s padrao do vigia
# podem cair em cima do retorno a posicao inicial, que roda DEPOIS de a
# telemetria parar: na sessao de 2026-09-15 o retorno levou 2 s, mas precisou de
# duas tentativas, e com mais tentativas os 25 s ficariam apertados.
VIGIA_DISPARO_S = 60.0
```

`Codigos/programas_principais/tracker.py`, linhas 85 a 93

```python
    vigia = iniciar_vigia() if args.vigia else None
    if vigia is not None:
        print(f"Vigia do mount ativo (pid {vigia.pid}), saida em {VIGIA_LOG}.")
    try:
        main(session_hours=args.horas, executar_autoteste=args.autoteste)
    finally:
        if vigia is not None and vigia.poll() is None:
            vigia.terminate()
            print("Vigia encerrado junto com a sessao.")
```

O arranjo é deliberado nos dois desfechos. Se o tracker termina direito, o
`finally` encerra o vigia. Se o tracker é morto sem rodar o `finally`, que é o
caso que o vigia existe para cobrir, o filho **sobrevive** e age sozinho. O
vigia roda em grupo de processos próprio, para o Ctrl+C do console não chegar
nele direto (`tracker.py`, linhas 59 a 69).

O coração do vigia:

`Codigos/programas_principais/vigia_mount.py`, linhas 126 a 154

```python
            elif idade <= args.armar_em:
                if not armado:
                    print(f"[{agora()}] ARMADO: tracker gravando.")
                    anotar("armado: tracker gravando")
                armado = True
                estado = f"gravando (telemetria de {idade:.0f} s atras)"
            elif armado and idade > args.disparar_em:
                print(f"\n[{agora()}] TRACKER PAROU de gravar ha {idade:.0f} s.")
                print(f"[{agora()}] subindo a escada de parada:")
                ok, degraus = escalar_parada(
                    porta_serial=args.porta_serial,
                    permitir_encerrar_servidor=args.encerrar,
                )
                print(
                    f"[{agora()}] "
                    + ("MOUNT PARADO." if ok else "FALHA: verifique o mount.")
                )
                anotar(
                    f"DISPAROU apos {idade:.0f} s sem telemetria: "
                    + ("parado" if ok else "FALHA ao parar, verifique o mount")
                )
                # Cada degrau no diario: de manha, saber ONDE a escada
                # resolveu diz qual protecao esta carregando o peso.
                for degrau in degraus:
                    anotar(f"  {degrau}")
                # Desarma para nao ficar repetindo; rearma sozinho se o
                # tracker voltar, o que cobre o operador reiniciando a sessao.
                armado = False
                estado = "disparado, aguardando nova sessao"
```

Duas regras simples fazem o vigia não atrapalhar:

- **Só arma depois de ver o tracker vivo**, com telemetria de menos de 15 s.
  Sem isso, ele pararia o mount no meio de uma calibração ou de um movimento
  manual, que também não escrevem telemetria de tracker.
- **Dispara uma vez e desarma.** Rearma sozinho se uma sessão nova começar.

Tempo de reação, somando tudo: até ~62 s para perceber (60 s de folga, mais o
intervalo de 2 s entre olhadas), e depois a escada da seção 7, que no pior caso
leva algumas dezenas de segundos. Da ordem de um minuto e meio, ou **5 a 6 arcmin** na velocidade
do micropulso.

> Revisado por Caio: ainda não

**Para conferir**

1. Por que o vigia observa a **telemetria** e não, por exemplo, se o processo
   do tracker ainda existe?
2. Por que o disparo foi aumentado de 25 s para 60 s? O que poderia dar errado
   com 25?
3. O retorno à posição inicial acontece **depois** de a telemetria parar e
   **antes** de o `finally` do `tracker.py` encerrar o vigia. O que aconteceria
   se um retorno longo passasse de 60 s?

---

## 7. A escada de parada

Até 2026-09-21 o vigia parava só pelo Alpaca, isto é, pelo servidor ASCOM. Isso
deixava um furo: se o servidor tivesse caído junto com o tracker, o vigia não
alcançava o mount por caminho nenhum. E o furo tinha uma ironia: justamente
nesse caso a porta serial fica **livre**, porque quem a segurava era o
servidor.

Os dois caminhos têm furos complementares:

| caminho | falha quando |
|---|---|
| Alpaca | o servidor ASCOM caiu ou travou |
| serial | o servidor ASCOM está de pé, porque ele toma a porta com exclusividade |

Encadeá-los fecha os dois:

`Codigos/modulos/controle/parada_emergencia.py`, linhas 202 a 230

```python
    # 1. Alpaca, o caminho normal e o unico que respeita o driver.
    try:
        if stop_axes_safely(attempts=3, timeout=3.0):
            registrar("1. Alpaca: eixos zerados")
            return True, degraus
        registrar("1. Alpaca: nao confirmou a parada")
    except Exception as exc:
        registrar(f"1. Alpaca: {type(exc).__name__}: {exc}")

    # 2. Serial direto. Pode funcionar sem passar pelo degrau 3 se o servidor
    #    ja tiver morrido sozinho, que e um cenario comum.
    ok, descricao = parar_pela_serial(porta_serial, baud)
    registrar(f"2. serial: {descricao}")
    if ok:
        return True, degraus

    if not permitir_encerrar_servidor:
        registrar("3. encerrar servidor: desabilitado por opcao")
        return False, degraus

    # 3. Derrubar o servidor para soltar a porta, e repetir a serial.
    ok_servidor, descricao = encerrar_servidor()
    registrar(f"3. encerrar servidor: {descricao}")
    if not ok_servidor:
        return False, degraus

    ok, descricao = parar_pela_serial(porta_serial, baud)
    registrar(f"4. serial apos encerrar: {descricao}")
    return ok, degraus
```

**Leia o degrau 3 com cuidado.** Encerrar o servidor **não para o eixo**; isso
está medido (os 266″ da seção 0). Ele entra só pelo efeito colateral de soltar
a porta serial, para o degrau 4 poder falar com o mount.

### A parada pela serial confere pela posição

`Codigos/modulos/controle/parada_emergencia.py`, linhas 109 a 122

```python
        with serial.Serial(porta, baud, timeout=timeout) as s:
            for comando in PARADAS:
                s.write(comando)
                time.sleep(0.05)
            # Confere movendo o relogio, nao a fe: duas leituras separadas. Se
            # a posicao mudar entre elas, algo ainda esta girando.
            antes = _ler_posicao(s)
            time.sleep(1.5)
            depois = _ler_posicao(s)
        if not antes or not depois:
            return False, f"{porta} respondeu vazio; parada NAO confirmada"
        if antes == depois:
            return True, f"parado e confirmado em {porta} (posicao {depois})"
        return False, f"AINDA EM MOVIMENTO em {porta}: {antes} -> {depois}"
```

Os comandos enviados são os cinco de parada do LX200:

`Codigos/modulos/controle/parada_emergencia.py`, linha 40

```python
PARADAS = (b":Q#", b":Qe#", b":Qw#", b":Qn#", b":Qs#")
```

O `:Q#` para tudo; os outros quatro param cada direção. Manda-se os cinco
porque o driver pode estar movendo por eixo ou por direção, e não vale a pena
adivinhar qual.

Aqui está a diferença para a seção 0: `stop_axes_safely` confia no servidor ter
**aceitado** o comando. A parada pela serial só declara sucesso se a **posição
parou de mudar** em 1,5 s. Essa conferência foi medida em 2026-09-14: com o
servidor ASCOM fechado e o azimute andando, a parada direta deteve o eixo
depois de 99″, e a altitude lida pela serial bateu com a do ASCOM.

### Dois cuidados de implementação

**A porta serial é descoberta**, não fixa. O mount responde em `COM5` num PC e
`COM6` no outro. A escada pergunta `:GVP#` em cada porta do sistema e usa a
primeira que responder (`parada_emergencia.py`, linhas 74 a 91).

**O servidor é localizado por quem escuta na porta do Alpaca**, via `netstat`,
e não pelo nome do programa, que muda conforme seja ASCOM Remote, driver
nativo ou simulador (`parada_emergencia.py`, linhas 129 a 155).

### O que nunca rodou em hardware

Isto é importante para a sua revisão. **Nunca foram exercidos com um mount de
verdade:**

- o degrau 3, encerrar o servidor, nem o degrau 4 depois dele;
- a escada inteira sendo disparada pelo vigia;
- a descoberta automática de porta encontrando o mount (só foi testada listando
  as portas do notebook, sem mount conectado).

O que já rodou é o equivalente ao degrau 2, pelo `parar_mount_direto.py`, no
teste de 2026-09-14. Um teste de bancada que vale fazer: tracker rodando,
fechar o servidor ASCOM na mão, e conferir no diário se o vigia atravessou até
o degrau 4.

> Revisado por Caio: ainda não

**Para conferir**

1. Em qual cenário a escada resolve já no degrau 2, sem precisar derrubar
   nada?
2. Por que não começar direto pela serial, que é o caminho mais robusto?
3. A parada pela serial lê a posição duas vezes com 1,5 s entre elas. O que ela
   concluiria de um mount andando a 3,75″/s? E a 0,001″/s?

---

## 8. A trava de uso: não parar o que não deve ser parado

Toda proteção que manda zero tem um risco simétrico: parar um uso legítimo. Uma
calibração de 9 minutos que morre no meio, ou uma sessão de madrugada que para
sem ninguém para reiniciar, é uma avaria criada pela proteção.

Em 2026-09-14 isso quase aconteceu: a trava olhava só a telemetria do tracker,
e uma parada direta rodada durante uma **calibração** passou reto, porque a
calibração não escreve telemetria de tracker. A pergunta certa é "alguém está
comandando o mount?", e não "o tracker está gravando?".

`Codigos/modulos/controle/mount_em_uso.py`, linhas 50 a 63

```python
def motivo_de_uso() -> str | None:
    """Descreve quem esta comandando o mount, ou ``None`` se ninguem estiver."""
    agora = time.time()

    marca = _mais_recente(TRACKER_SESSOES, "*/telemetria.csv")
    if marca is not None and agora - marca < TRACKER_VIVO_S:
        return f"tracker gravando ha {agora - marca:.0f} s"

    # Qualquer arquivo da corrida serve: auditoria de varredura, bins, imagem.
    marca = _mais_recente(CALIBRACAO_RUNS, "*/**/*")
    if marca is not None and agora - marca < CALIBRACAO_VIVA_S:
        return f"calibracao escreveu ha {agora - marca:.0f} s"

    return None
```

As janelas são diferentes de propósito: o tracker escreve a cada segundo, então
30 s cobrem qualquer engasgo; a calibração grava por varredura, e cada
varredura leva dezenas de segundos, então precisa de 180 s.

Na dúvida, a trava responde que **não** está em uso: deixar de parar um mount à
deriva é pior do que uma leitura de disco que falhou.

### O furo das pastas, fechado em 2026-09-23

A trava e o vigia só funcionam se olharem **a mesma pasta** em que o tracker
grava. Até 2026-09-23 isso só valia para a IDS: com a ASI, o tracker gravava a
telemetria em outra pasta, e numa sessão com ela o vigia nunca teria armado.
Agora os dois lados leem os caminhos de um lugar só:

`Codigos/modulos/configuracoes/saidas.py`, linhas 35 e 36

```python
TRACKER_SESSOES_DIR = TRACKER_OUTPUT_DIR / "sessoes"
CALIBRACAO_RUNS_DIR = CALIBRATION_OUTPUT_DIR / "continua"
```

Testado só em software: os três perfis de câmera gravam onde o vigia olha.
**Nunca rodou numa sessão real com a ASI.**

> Revisado por Caio: ainda não

**Para conferir**

1. Quem consulta a trava? (Procure `motivo_de_uso` no código.)
2. Por que a resposta "na dúvida, não está em uso" é a escolha segura, e não a
   oposta?
3. Durante o retorno à posição inicial a telemetria já parou. Passados 30 s, a
   trava diria que o mount está livre. Se alguém rodasse a parada direta nesse
   momento, o que aconteceria? Isso seria um problema?

---

## 9. As tarefas de boot

Tudo até aqui depende de o Windows estar de pé. Se o PC reinicia, sobram as
tarefas agendadas para rodar na inicialização. São duas, com caminhos
diferentes:

| programa | caminho até o mount | precisa de alguém logado? |
|---|---|---|
| `parar_mount.py` | Alpaca, pelo servidor ASCOM | **sim**, o servidor é programa de janela |
| `parar_mount_direto.py` | serial, LX200 | **não**, roda como SYSTEM |

O `parar_mount.py` insiste até conseguir, porque no boot o servidor ASCOM quase
nunca subiu ainda. E ele distingue "servidor fora do ar" de "mount ignorou o
comando", que antes davam o mesmo alarme:

`Codigos/programas_principais/parar_mount.py`, linhas 64 a 81

```python
def tentar_parar() -> tuple[bool, str]:
    """Uma tentativa. Devolve (parou, descricao do que aconteceu)."""
    # Sonda o servidor antes de mandar parar. Sem isto os dois desfechos ruins
    # sao indistinguiveis: stop_axes_safely engole a excecao de cada eixo e
    # devolve False tanto quando o servidor esta fora do ar quanto quando o
    # mount ignora o comando. No boot de 2026-09-14 isso gerou um "verifique o
    # mount FISICAMENTE" quando o problema era so o ASCOM ainda nao ter subido,
    # e um alarme desses a cada reinicio ensina o operador a ignorar alarmes.
    try:
        call("GET", "connected", timeout=3.0)
    except Exception as exc:
        return False, f"servidor ASCOM fora do ar: {type(exc).__name__}: {exc}"
    try:
        if stop_axes_safely(attempts=3, timeout=3.0):
            return True, "eixos confirmados em velocidade zero"
        return False, "ALERTA: servidor no ar mas os eixos nao confirmaram parada"
    except Exception as exc:
        return False, f"sem contato com o mount: {type(exc).__name__}: {exc}"
```

Numa conta com senha, depois de um reinício ninguém faz login, e o
`parar_mount.py` nunca alcança o mount. Por isso existe o `parar_mount_direto`,
que fala pela serial e não precisa de ninguém. A janela de reação do boot é de
60 a 120 s, ou 4 a 8 arcmin na velocidade do micropulso.

Todos os que param deixam rastro no mesmo diário,
`Codigos/resultados/parar_mount.txt`: o vigia, a parada direta e a tarefa de
boot. É o primeiro lugar para olhar depois de uma noite estranha.

**Não verifiquei daqui quais tarefas estão de fato registradas no PC da UFF.**
Para conferir lá:

```powershell
schtasks /query /fo LIST /v | Select-String -Pattern "parar_mount" -Context 0,12
```

> Revisado por Caio: ainda não

**Para conferir**

1. Por que as duas tarefas, e não só a direta, que funciona sem login?
2. O PC reinicia às 3h por Windows Update com o tracker rodando. Siga a
   sequência: quais proteções das seções 2 a 9 disparam, e quais não?

---

## 10. O que continua sem cobertura

| cenário | por que nada age |
|---|---|
| PC travado de vez, sem reiniciar | todo o software de proteção roda nele |
| PC desligado que não volta | não há boot, então não há tarefa de boot |
| queda de energia | coberto por acaso: o mount perde energia junto e para |

Os dois primeiros só têm solução com hardware: uma tomada inteligente, que
corta a energia do mount ou do PC de longe, ou um segundo computador na mesma
rede do mount. O Wi-Fi do AM5 foi avaliado e **não vira proteção sozinho**: para
mandar o `:Q#` por ele, alguém precisa estar vivo na rede local do mount, e a
única máquina nossa lá é justamente a que travou (roteiro, "Por que o Wi-Fi do
mount não entra aqui").

Quanto tempo o mount passa de fato exposto, com um pulso em curso: na sessão
de 10 h de 2026-09-15, **116 das 167 337 linhas de telemetria** estavam na fase
`pulso`, ou 0,069%, uns 25 s na noite inteira. É uma estimativa por amostragem,
com incerteza da ordem de 10%, e vale porque a telemetria (~4,6 Hz) não anda no
ritmo dos pulsos. Ela bate com um limite independente: 505 ciclos de correção
vezes a duração de um pulso, entre 22 e 120 ms, dão de 11 a 61 s. O cenário
ruim exige o PC morrer exatamente nessa fração e não voltar.

---

## Divergências encontradas ao escrever este documento

Registradas para você saber o que mudou por causa desta revisão:

1. **Eu tinha dito, numa conversa, que o fim pelo tempo não voltava à posição
   inicial. Está errado**, e a seção 3 mostra por quê. A consequência prática:
   o plano de "rodar o tracker 15 minutos para centralizar e depois observar
   sem corrigir" não funciona como estava, porque no fim dos 15 minutos o mount
   volta para onde começou.
2. Três comentários no código estavam desatualizados e foram corrigidos no
   commit `332b9e1`: o pior caso de 360 °/h no `vigia_mount.py` e no
   `parar_mount.py`, que hoje é 18 °/h; e o `desligamento_windows.py` dizendo
   que cobria o reinício do Windows Update, quando o próprio roteiro mediu que
   não cobre.
