/**
 * useLiveMarket — thin alias for the shared LivePricesContext.
 *
 * All consumers that called useLiveMarket() now read from the single
 * app-level WebSocket connection instead of each opening their own.
 */
export { useLivePrices as useLiveMarket } from '../contexts/LivePricesContext';
