# Protocolo do AM5: o que sabemos, e de onde

**O que este documento é.** A lista de comandos que o driver INDI `lx200am5.cpp`
manda para o AM5, extraída do próprio código-fonte em 2026-09-16, mais o que
medimos no equipamento.

**O que ele não é.** Não é a documentação oficial da ZWO, que não é pública. E
não é o conjunto completo de comandos que o firmware aceita, por dois motivos:

1. O driver implementa só o que o INDI precisa. O firmware pode aceitar mais.
2. `LX200AM5` herda de `LX200Generic`, então os comandos básicos do LX200
   (`:Q#`, `:Me#`, `:GZ#`, `:GA#`, `:GR#`, `:GD#` e outros) vêm da classe mãe e
   **não aparecem** nesta lista. Nós usamos vários deles, e eles funcionam.

Fonte: https://github.com/indilib/indi/blob/master/drivers/telescope/lx200am5.cpp

**Dois mounts, firmwares diferentes.** O da UFF roda a versão de 14/07/2025 e o
do laboratório a de 25/01/2026. Eles respondem diferente a `:GU#`, `:Gh#` e
`:GAT#`, então um resultado medido num não vale automaticamente no outro.

---

## Comandos específicos do AM5

Os 34 literais encontrados no arquivo, com o formato exato de `printf`.

### Modo de alinhamento e estado

| comando | o que faz |
|---|---|
| `:AA#` | põe o mount em modo azimutal |
| `:AP#` | põe em modo equatorial |
| `:GU#` | estado resumido do mount |
| `:Gm#` | lado do pilar |
| `:GT#` | modo de rastreio |
| `:GAT#` | rastreio está ativo? |
| `:Te#` / `:Td#` | liga / desliga o rastreio |

Medido em 2026-09-16: `:GU#` devolveu `nNZtM000000440#`. Não decodificamos.

### Taxas

| comando | o que faz |
|---|---|
| `:R%d#` | taxa de slew por índice de classe |
| `:Rg%.2f#` | taxa de guiding, **valor numérico com 2 decimais** |
| `:Ggr#` | consulta a taxa de guiding |
| `:Rv%.2f#` | taxa de slew **variável**, numérica com 2 decimais |

Medido: `:Ggr#` devolveu `0.50`, ou seja meia taxa sideral.

Isto derruba a objeção de que o LX200 só ofereceria classes discretas: `:Rv` e
`:Rg` aceitam valor contínuo. Com 2 casas decimais, o passo é de 0,01 do que
quer que seja a unidade, provavelmente múltiplos da taxa sideral, o que daria
0,15 arcsec/s.

### Limites de altitude

| comando | o que faz |
|---|---|
| `:SLE#` / `:SLD#` | habilita / desabilita o controle de limite |
| `:GLC#` | o controle está habilitado? |
| `:SLL%02d#` | escreve o limite inferior, **inteiro de 2 dígitos** |
| `:GLL#` | lê o limite inferior |
| `:SLH%02d#` | escreve o limite superior |
| `:GLH#` | lê o limite superior |

Medido em 2026-09-16, antes de qualquer escrita:

```
:GLC#  '0#'    controle DESLIGADO
:GLL#  '0#'    inferior   0 graus
:GLH#  '90#'   superior  90 graus
```

O driver considera a escrita aceita quando a resposta é `'1'`.

**Resolução de 1 grau**, e só altitude: não há equivalente para azimute.

**Cuidado que o próprio resultado revelou:** o enlace opera a −0,042°, já abaixo
do limite inferior atual de 0. Habilitar sem antes baixar o limite deixaria o
mount fora da própria faixa, e não há documentação do que o firmware faz nesse
caso. Por isso `limite_altitude_firmware.py` recusa habilitar sem folga.

#### O firmware recusa limite inferior negativo: medido em 2026-09-16

Testado no mount do laboratório, firmware de 25/01/2026, com o controle de
limite desligado e restaurando 0 no fim:

```
:SLL01#   respondeu '1'   lê 1   ACEITO
:SLL05#   respondeu '1'   lê 5   ACEITO
:SLL-1#   respondeu '0'   lê 5   recusado
:SLL-01#  respondeu '0'   lê 5   recusado
:SLL-5#   respondeu '0'   lê 5   recusado
:SLL00#   respondeu '1'   lê 0   ACEITO
```

O caminho de escrita funciona: positivos são aceitos e a leitura confirma. O
firmware recusa valores negativos nas três variantes de formato, então não era o
formato, era o valor.

**Consequência: o limite de altitude não serve como proteção neste
experimento.** A altitude reportada zera a cada ligamento do mount, e o enlace
opera com ela em torno de 0. Um limite inferior de 0 dispararia na primeira
correção para baixo, e o tracker faz muitas: na sessão de 10 h de 2026-09-15 a
altitude terminou 10 arcsec abaixo do início.

Existe uma saída teórica, registrada para não ser redescoberta: ligar o mount
apontado cerca de 1° abaixo do beacon e subir 1° para adquirir faria a posição
de operação ler +1°, e aí um limite em 0 protegeria contra 1° de deriva para
baixo. Custa um procedimento de apontamento a cada ligamento, com reaquisição do
beacon, e cria o risco de alguém ligar errado e o limite disparar no meio de uma
sessão longa. Não compensa.

### Posição e movimento

| comando | o que faz |
|---|---|
| `:hC#` | vai para a posição home |
| `:hP#` | estaciona (o driver não usa; voltou a usar home) |
| `:SOa#` | grava a posição atual como home |

**Não usar `:hC#` nem `:hP#` neste experimento.** Com o conjunto apontado quase
na horizontal para um alvo a 7 km, cabos presos e câmera montada, um movimento
para a posição mecânica de origem pode encontrar a estrutura no caminho.

### Configuração

| comando | o que faz |
|---|---|
| `:SBu%d#` / `:GBu#` | buzzer, 0 desligado, 1 baixo, 2 alto |
| `:SRl720#` / `:SRl1440#` / `:GRl#` | modo heavy duty |
| `:STa%d%d%c%02d#` / `:GTa#` | parâmetros do meridian flip |
| `:NSC#` | apaga o alinhamento multiestrela |
| `:SG%c%02d:%02d#` | fuso em relação ao UTC |
| `:SC%02d/%02d/%02d#` | data local |
| `:Sg%c%03d*%02d:%02d#` | longitude |
| `:St%c%02d*%02d:%02d#` | latitude |

---

## O que NÃO existe

Busca por `watchdog`, `heartbeat`, `keepalive`, `timeout`, `deadman`, `safety`,
`emergency` no fonte inteiro: **nenhuma ocorrência**. Só aparece `Abort`, uma
vez, e é o método herdado que manda o `:Q#` da classe mãe.

Não há comando de movimento com duração, nem keep-alive, nem parada automática
por perda de conexão.

Isso é consistente com as duas medidas independentes no equipamento:

- `PulseGuide` do ASCOM: `CanPulseGuide = True`, não move
- `:Mg<direção><ms>` cru: deslocamento zero em 200, 500, 1000 e 2000 ms, nos
  dois sentidos, quando a taxa de guiding reportada previa de 1,5 a 15 arcsec

**Ressalva honesta:** ausência no driver não é prova de ausência no firmware. O
que se pode afirmar é que, se existe, o INDI não usa e não há documentação
pública. A única forma de fechar isso seria a ZWO publicar o protocolo.

---

## Consequência para o projeto

Não há pulso autolimitado neste mount. A proteção contra deriva depende
inteiramente das camadas de software que construímos, e o único candidato a
proteção no firmware é o limite de altitude, ainda por testar.
