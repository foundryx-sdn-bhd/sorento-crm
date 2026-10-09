const { chromium } = require('playwright-core');
const fs = require('fs');
const dir = process.argv[2];
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  for (const c of ['case1', 'case2', 'case3']) {
    const p = await b.newPage({ viewport: { width: 698, height: 1000 } });
    await p.setContent(fs.readFileSync(`${dir}/${c}.html`, 'utf8'));
    const m = await p.evaluate(() => {
      const t = [...document.querySelectorAll('table[style*="font-size:12px"]')];
      const cells = t.flatMap(x => [...x.querySelectorAll('td, th')]);
      const multi = cells.filter(td => td.getBoundingClientRect().height > 16 * 2 + 4).map(td => td.textContent.trim());
      const card = document.querySelector('.em-card').getBoundingClientRect().width;
      return { scrollWidth: document.documentElement.scrollWidth, card, rows: t[1].querySelectorAll('tbody tr').length, multi: [...new Set(multi)] };
    });
    await p.emulateMedia({ media: 'print' });
    const pdf = await p.pdf({ preferCSSPageSize: true, printBackground: true });
    const pages = (pdf.toString('latin1').match(/\/Type\s*\/Page[^s]/g) || []).length;
    fs.writeFileSync(`${dir}/${c}.pdf`, pdf);
    console.log(JSON.stringify({ case: c, ...m, pdfPages: pages }));
    await p.close();
  }
  await b.close();
})();
