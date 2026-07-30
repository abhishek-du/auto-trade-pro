import puppeteer from 'puppeteer';

(async () => {
  const browser = await puppeteer.launch({ headless: "new", args: ['--no-sandbox', '--disable-setuid-sandbox'] });
  const page = await browser.newPage();
  await page.goto('http://localhost:5173/trades', { waitUntil: 'networkidle0' });
  
  // Wait for the table to load
  await page.waitForSelector('table');
  
  // Type in the search box
  await page.type('input[placeholder="Search symbol…"]', 'AASTHA');
  await new Promise(r => setTimeout(r, 1000));
  
  const html = await page.evaluate(() => document.querySelector('tbody').innerText);
  console.log("Table content for AASTHA:");
  console.log(html);
  
  await browser.close();
})();
