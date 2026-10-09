const { chromium } = require('playwright-core');
const fs = require('fs');
const dir = process.argv[2];
(async () => {
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium-1194/chrome-linux/chrome' });
  for (const c of ['case1', 'case2', 'case3', 'case4', 'case5', 'case6', 'case7', 'case8']) {
    const p = await b.newPage({ viewport: { width: 698, height: 1000 } });
    await p.setContent(fs.readFileSync(`${dir}/${c}.html`, 'utf8'));
    const m = await p.evaluate(() => {
      const t = [...document.querySelectorAll('table[style*="font-size"]')];
      const cells = t.flatMap(x => [...x.querySelectorAll('td, th')]);
      const multi = cells.filter(td => td.getBoundingClientRect().height > (parseFloat(getComputedStyle(td).lineHeight)||16) * 2 + 4).map(td => td.textContent.trim());
      const card = document.querySelector('.em-card').getBoundingClientRect().width;
      const remarkLines = [...t[1].querySelectorAll('tbody tr')].filter(r => r.children.length > 1).map(r => {
        const td = r.lastElementChild; const lh = parseFloat(getComputedStyle(td).lineHeight) || 16;
        const pad = parseFloat(getComputedStyle(td).paddingTop) + parseFloat(getComputedStyle(td).paddingBottom);
        return [td.textContent.trim(), Math.round((td.getBoundingClientRect().height - pad) / lh)];
      }).filter(([, n]) => n > 1);
      const colW = [...t[1].querySelectorAll('thead th')].map(th => th.textContent.trim() + ':' + Math.round(th.getBoundingClientRect().width));
      const tableW = Math.round(t[1].getBoundingClientRect().width);
      const remarkTd = [...t[1].querySelectorAll('tbody tr')].filter(r => r.children.length > 1).map(r => r.lastElementChild);
      const remarkSlack = Math.min(...remarkTd.map(td => { const rg = document.createRange(); rg.selectNodeContents(td); return Math.round(td.getBoundingClientRect().width - rg.getBoundingClientRect().width); }));
      const head = t[0];
      const headCols = [...head.querySelectorAll('thead th')].map(th => th.textContent.trim() + ':' + Math.round(th.getBoundingClientRect().width));
      const headLines = [...head.querySelectorAll('tbody td')].map(td => { const lh = parseFloat(getComputedStyle(td).lineHeight) || 16; const rg = document.createRange(); rg.selectNodeContents(td); return [td.textContent.trim().slice(0, 24), Math.round(rg.getBoundingClientRect().height / lh)]; });
      return { headCols, headLines, colW, tableW, remarkSlack, remarkLines, scrollWidth: document.documentElement.scrollWidth, card, rows: t[1].querySelectorAll('tbody tr').length, multi: [...new Set(multi)] };
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
