import logging
import asyncio
from bs4 import BeautifulSoup
from playwright.async_api import async_playwright
from telegram import Bot

# ==============================================================================
# CONFIGURAÇÕES DO MONITOR
# ==============================================================================
TELEGRAM_TOKEN = "8840915052:AAHPrjICol3JAKf_u9MdZ6-W4VYpi214soY"
CHAT_ID = "5422544561"  # Chat ID

NOME_DEPTO = "CAMPUS UNB GAMA: FACULDADE DE CIÊNCIAS E TECNOLOGIAS EM ENGENHARIA - BRASÍLIA"  # Nome exato da unidade no menu do SIGAA
CODIGO_DISCIPLINA = "FGA0142"
TURMAS_ALVO = ["01", "02"]

INTERVALO_SEGUNDOS = 60

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
vagas_notificadas = {}


# ==============================================================================
# EXTRATOR VIA NAVEGADOR
# ==============================================================================
async def consultar_sigaa_playwright(p):
    url_home = "https://sigaa.unb.br/sigaa/public/home.jsf"

    browser = await p.chromium.launch(headless=True)
    page = await browser.new_page()

    try:
        await page.goto(url_home, timeout=30000)

        try:
            btn_ciente = page.locator('button:has-text("Ciente")')
            await btn_ciente.wait_for(state="visible", timeout=3000)
            await btn_ciente.click()
            logging.info("🛡️ Banner de cookies fechado.")
        except Exception:
            pass

        await page.click("text=Ensino")
        await page.click("text=Turmas")

        # Garante que o select do departamento apareceu na tela
        await page.wait_for_selector("select[name*='inputDepto']", timeout=15000)

        # 1. Seleciona Nível (Graduação)
        await page.select_option("select[name*='inputNivel']", value="G")

        # 2. Aguarda o select de departamento ser habilitado/populado pelo AJAX da UnB
        await page.wait_for_function(
            "() => document.querySelector(\"select[name*='inputDepto']\").options.length > 1",
            timeout=10000
        )

        # 3. Seleciona a Unidade do Gama
        await page.select_option(
            "select[name*='inputDepto']",
            label="CAMPUS UNB GAMA: FACULDADE DE CIÊNCIAS E TECNOLOGIAS EM ENGENHARIA - BRASÍLIA"
        )

        try:
            await page.fill("input[name*='inputAno']", "2026")
            await page.select_option("select[name*='inputPeriodo']", value="2")
        except Exception:
            pass

        # 4. Clica em Buscar
        await page.click("input[value='Buscar']")

        # 5. Aguarda a tabela de resultados aparecer E ter conteúdo de fato dentro dela
        await page.wait_for_selector("table.listagem tr", timeout=45000)

        # Pausa extra de segurança para carregar todas as linhas da tabela
        await page.wait_for_timeout(2000)

        # 📸 Tira um print do resultado para conferirmos
        await page.screenshot(path="resultado_busca.png")
        logging.info("📸 Tabela carregada com sucesso!")

        html_content = await page.content()
        await browser.close()
        return html_content

    except Exception as e:
        logging.error(f"Erro ao interagir com a página do SIGAA: {e}")
        await browser.close()
        return None


def processar_turmas_fga0142(html_content):
    if not html_content:
        return []

    soup = BeautifulSoup(html_content, "html.parser")
    tabela = soup.find("table", {"class": "listagem"})
    if not tabela:
        return []

    resultado = []
    disciplina_atual = ""

    for tr in tabela.find_all("tr"):
        th_elem = tr.find("th")
        tds = tr.find_all("td")

        texto_tr = tr.text.strip()
        if th_elem or len(tds) <= 2:
            if " - " in texto_tr and any(p in texto_tr.upper() for p in ["FGA", "FCTE", "CIC", "ENM", "ENF"]):
                disciplina_atual = texto_tr
                continue

        if len(tds) >= 7:
            if CODIGO_DISCIPLINA.upper() in disciplina_atual.upper():
                codigo_turma = tds[0].text.strip()
                horario = tds[3].text.strip()

                try:
                    txt_ofertadas = "".join(filter(str.isdigit, tds[5].text))
                    txt_ocupadas = "".join(filter(str.isdigit, tds[6].text))

                    vagas_ofertadas = int(txt_ofertadas) if txt_ofertadas else 0
                    vagas_ocupadas = int(txt_ocupadas) if txt_ocupadas else 0
                    vagas_restantes = vagas_ofertadas - vagas_ocupadas
                except Exception:
                    vagas_restantes = 0

                turmas_filtro = globals().get("TURMAS_ALVO", [])
                if not turmas_filtro or any(t in codigo_turma for t in turmas_filtro):
                    resultado.append({
                        "disciplina": disciplina_atual,
                        "turma": codigo_turma,
                        "horario": horario,
                        "vagas": vagas_restantes
                    })

    return resultado

# ==============================================================================
# MONITOR
# ==============================================================================
async def main():
    bot = Bot(token=TELEGRAM_TOKEN)
    logging.info(f"Monitor Playwright ativado! Acompanhando {CODIGO_DISCIPLINA.upper()} a cada {INTERVALO_SEGUNDOS}s...")

    async with async_playwright() as p:
        while True:
            html = await consultar_sigaa_playwright(p)

            if html:
                turmas = processar_turmas_fga0142(html)

                if not turmas:
                    logging.warning(
                        "Nenhuma turma encontrada. Verifique se o nome do departamento no select é CAMPUS UNB GAMA.")

                for t in turmas:
                    chave = t['turma']
                    qtd_vagas = t['vagas']

                    logging.info(f"Status SIGAA | {t['turma']} -> Vagas restantes: {qtd_vagas}")

                    if qtd_vagas > 0 and vagas_notificadas.get(chave, 0) != qtd_vagas:
                        msg = (
                            f"🚨 **VAGA LIBERADA NO SIGAA!**\n\n"
                            f"📚 **Matéria:** {t['disciplina']}\n"
                            f"🏫 **Turma:** {t['turma']}\n"
                            f"⏰ **Horário:** {t['horario']}\n"
                            f"🔥 **Vagas Restantes:** {qtd_vagas}\n\n"
                            f"🏃‍♂️ Acesse o SIGAA e garanta sua vaga!"
                        )
                        await bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
                        vagas_notificadas[chave] = qtd_vagas

            await asyncio.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    asyncio.run(main())
