const puppeteer = require('puppeteer');
const fs = require('fs');

const PAGES = [
    { name: "Dashboard", route: "/" },
    { name: "Trades", route: "/trades" },
    { name: "Analytics", route: "/analytics" },
    { name: "News", route: "/news" },
    { name: "Simulation", route: "/simulation" },
    { name: "Settings", route: "/settings" },
    { name: "Documentation", route: "/documentation" },
    { name: "IndiaMarket", route: "/india" },
    { name: "IndiaSignals", route: "/india/signals" },
    { name: "FnO", route: "/fno" },
    { name: "FnOPipelineFlow", route: "/fno-pipeline" },
    { name: "MutualFunds", route: "/mutual-funds" },
    { name: "IndiaFundamentals", route: "/fundamentals" },
    { name: "Backtest", route: "/backtest" },
    { name: "Portfolio", route: "/portfolio" },
    { name: "Zerodha", route: "/zerodha/connect" },
    { name: "LiveMarket", route: "/live-market" },
    { name: "Watchlist", route: "/watchlist" },
    { name: "Chart", route: "/chart" },
    { name: "MarketBreadth", route: "/market-breadth" },
    { name: "SectorHeatmap", route: "/sector-heatmap" },
    { name: "MarketCalendar", route: "/calendar" },
    { name: "PortfolioTracker", route: "/portfolio-tracker" },
    { name: "SIPTracker", route: "/sip" },
    { name: "TaxCalculator", route: "/tax" },
    { name: "AssetAllocation", route: "/allocation" },
    { name: "IPOTracker", route: "/ipo" },
    { name: "StockChat", route: "/chat" },
    { name: "PortfolioDoctor", route: "/doctor" },
    { name: "EarningsAnalyzer", route: "/earnings" },
    { name: "TradingAgent", route: "/agent" },
    { name: "AgentLog", route: "/agent-log" },
    { name: "MarketScanner", route: "/discover/scanner" },
    { name: "IntelligenceDashboard", route: "/intelligence" },
    { name: "PipelineFlow", route: "/pipeline" },
    { name: "PortfolioAnalytics", route: "/portfolio-analytics" },
    { name: "BuybackTracker", route: "/buyback" }
];

(async () => {
    if (!fs.existsSync('screenshots')) {
        fs.mkdirSync('screenshots');
    }

    const browser = await puppeteer.launch({
        headless: "new",
        args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    
    const page = await browser.newPage();
    await page.setViewport({ width: 1920, height: 1080 });

    for (let p of PAGES) {
        console.log(`Capturing ${p.name}...`);
        try {
            await page.goto(`http://localhost:5173${p.route}`, { waitUntil: 'domcontentloaded', timeout: 15000 });
            // Wait an extra 3 seconds for React animations/charts to settle
            await new Promise(resolve => setTimeout(resolve, 3000));
            
            // Try typing admin into password if we got redirected to login
            const isLogin = await page.evaluate(() => window.location.href.includes('/login'));
            if (isLogin) {
                console.log('Redirected to login. Attempting to bypass or capture login state.');
                const passInput = await page.$('input[type="password"]');
                if (passInput) {
                    await passInput.type('admin');
                    await page.keyboard.press('Enter');
                    await new Promise(resolve => setTimeout(resolve, 3000));
                }
            }

            await page.screenshot({ path: `screenshots/${p.name}.png`, fullPage: true });
        } catch (e) {
            console.log(`Failed to capture ${p.name}: ${e.message}`);
        }
    }

    await browser.close();
})();
