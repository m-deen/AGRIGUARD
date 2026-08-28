/**
 * Shared GoBid-style catalog and lot rendering for AgriGuard auctions.
 */
(function (global) {
  function photoClass(species) {
    const key = String(species || '').toLowerCase();
    if (key === 'goat') return 'goat';
    if (key === 'sheep') return 'sheep';
    return '';
  }

  function tagHtml(auction) {
    return AgriAuction.lotTags(auction).map((tag) => {
      const extra = tag === 'Hot Interest' ? ' hot' : tag === 'Your listing' ? ' mine' : '';
      return `<span class="lot-tag${extra}">${tag}</span>`;
    }).join('');
  }

  function watchButton(auction, showWatch) {
    if (!showWatch) return '';
    const watched = AgriAuction.isWatched(auction.auction_id);
    return `
      <button class="lot-watch ${watched ? 'active' : ''}" title="${watched ? 'Remove from watchlist' : 'Watch this lot'}" onclick="AgriAuctionUI.toggleWatch(event, ${auction.auction_id})">
        <svg class="ag-icon" viewBox="0 0 24 24" fill="${watched ? 'currentColor' : 'none'}" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 2l3.1 6.3L22 9.3l-5 4.9 1.2 6.8L12 17.8 5.8 21l1.2-6.8-5-4.9 6.9-1L12 2z"/></svg>
      </button>
    `;
  }

  function card(auction, options) {
    const opts = options || {};
    return `
      <article class="lot-card" onclick="AgriAuctionUI.openLot(${auction.auction_id})">
        <div class="lot-photo ${photoClass(auction.species)}">
          ${AgriAuction.speciesIcon(auction.species)}
          <div class="lot-count" data-countdown="${auction.auction_end}">${AgriAuction.formatCountdown(auction.auction_end)}</div>
          ${watchButton(auction, opts.showWatch)}
        </div>
        <div class="lot-body">
          <div class="lot-title">${auction.title}</div>
          <div>
            <div class="lot-bid-label">${AgriAuction.bidLabel(auction)}</div>
            <div class="lot-bid-value">${AgriAuction.formatMoney(auction.current_bid || auction.starting_price)}</div>
          </div>
          <div class="lot-expires">Expires ${AgriAuction.formatExpires(auction.auction_end)}</div>
          <div class="lot-meta">${auction.yard || auction.seller_name || 'AgriGuard'} · ${auction.location || 'South Africa'} · Lot ${auction.lot_no || auction.auction_id}</div>
          <div class="lot-tags">${tagHtml(auction)}</div>
        </div>
      </article>
    `;
  }

  function renderGrid(container, auctions, options) {
    if (!container) return;
    if (!auctions.length) {
      container.innerHTML = '<div class="empty-state">No lots found for this search.</div>';
      return;
    }
    container.innerHTML = auctions.map((auction) => card(auction, options)).join('');
  }

  function bidHistoryHtml(auctionId) {
    const bids = AgriAuction.getBids(auctionId);
    if (!bids.length) return '<p style="font-size:13px;color:#6b7280;">No bids yet. Be the first.</p>';
    return bids.slice(0, 8).map((bid, index) => `
      <div class="bid-history-row ${index === 0 ? 'top' : ''}">
        <span>${index === 0 ? 'Leading · ' : ''}${bid.buyer}</span>
        <span>${AgriAuction.formatMoney(bid.amount)}</span>
      </div>
    `).join('');
  }

  function lotHtml(auction, options) {
    const opts = options || {};
    const minBid = AgriAuction.minBidFor(auction);
    const ended = auction.status !== 'active';
    let action = '';
    if (ended) {
      action = '<div class="alert">This lot has expired.</div>';
    } else if (auction.mine && opts.role === 'farmer') {
      action = '<div class="alert alert-success">This is your listing. Buyers bid from the marketplace; incoming bids appear below.</div>';
    } else {
      action = `
        <div class="form-group">
          <label>Your bid (min ${AgriAuction.formatMoney(minBid)})</label>
          <input type="number" id="bidAmount" class="form-input" value="${minBid}" min="${minBid}" step="50">
        </div>
        <div class="bid-increments">
          <button type="button" onclick="AgriAuctionUI.bumpBid(100)">+ R100</button>
          <button type="button" onclick="AgriAuctionUI.bumpBid(250)">+ R250</button>
          <button type="button" onclick="AgriAuctionUI.bumpBid(500)">+ R500</button>
        </div>
        <button class="btn-bid" onclick="AgriAuctionUI.placeBid()">Place Bid</button>
      `;
    }

    return `
      <button class="lot-back" onclick="AgriAuctionUI.closeLot()">← Back to timed auctions</button>
      <div class="lot-layout">
        <div>
          <div class="lot-hero lot-photo ${photoClass(auction.species)}">
            ${AgriAuction.speciesIcon(auction.species)}
            <div class="lot-count" data-countdown="${auction.auction_end}">${AgriAuction.formatCountdown(auction.auction_end)}</div>
          </div>
          <div class="lot-specs">
            <h2>${auction.title}</h2>
            <p class="lot-desc">${auction.description || 'No additional notes for this lot.'}</p>
            <table>
              <tr><th>Lot</th><td>${auction.lot_no || auction.auction_id}</td></tr>
              <tr><th>Species</th><td>${auction.species}</td></tr>
              <tr><th>Breed</th><td>${auction.breed}</td></tr>
              <tr><th>Yard</th><td>${auction.yard || auction.seller_name || 'AgriGuard'}</td></tr>
              <tr><th>Location</th><td>${auction.location || 'South Africa'}</td></tr>
              <tr><th>Estimate</th><td>${AgriAuction.formatMoney(auction.predicted_price)}</td></tr>
            </table>
          </div>
        </div>
        <aside class="bid-box">
          <div class="bid-kicker">${AgriAuction.bidLabel(auction)}</div>
          <div class="bid-big">${AgriAuction.formatMoney(auction.current_bid || auction.starting_price)}</div>
          <div class="bid-expire">
            <span>Expires ${AgriAuction.formatExpires(auction.auction_end)}</span>
            <span data-countdown="${auction.auction_end}">${AgriAuction.formatCountdown(auction.auction_end)}</span>
          </div>
          ${action}
          <div id="bidMsg"></div>
          <div class="bid-history">
            <h3>Bid history</h3>
            ${bidHistoryHtml(auction.auction_id)}
          </div>
        </aside>
      </div>
    `;
  }

  let tickTimer = null;
  let currentLotId = null;
  let uiOptions = { role: 'buyer', showWatch: true, onChange: null };

  function tickCountdowns() {
    document.querySelectorAll('[data-countdown]').forEach((node) => {
      node.textContent = AgriAuction.formatCountdown(node.getAttribute('data-countdown'));
    });
  }

  function startTick() {
    if (tickTimer) clearInterval(tickTimer);
    tickTimer = setInterval(tickCountdowns, 1000);
  }

  function catalogEl() {
    return document.getElementById('catalogView');
  }

  function lotEl() {
    return document.getElementById('lotView');
  }

  function setUrl(id) {
    if (!window.history.replaceState) return;
    const url = new URL(window.location.href);
    if (id) url.searchParams.set('id', id);
    else url.searchParams.delete('id');
    window.history.replaceState({}, '', url);
  }

  function openLot(auctionId) {
    if (!lotEl()) {
      window.location.href = 'auctions.html?id=' + auctionId;
      return;
    }
    const auction = AgriAuction.getAuction(auctionId);
    if (!auction) return;
    currentLotId = auction.auction_id;
    const catalog = catalogEl();
    const lot = lotEl();
    if (catalog) catalog.style.display = 'none';
    if (lot) {
      lot.classList.add('show');
      lot.innerHTML = lotHtml(auction, uiOptions);
    }
    setUrl(auction.auction_id);
    window.scrollTo(0, 0);
  }

  function closeLot() {
    currentLotId = null;
    const catalog = catalogEl();
    const lot = lotEl();
    if (catalog) catalog.style.display = '';
    if (lot) {
      lot.classList.remove('show');
      lot.innerHTML = '';
    }
    setUrl(null);
    if (typeof uiOptions.onChange === 'function') uiOptions.onChange();
  }

  function bumpBid(delta) {
    const input = document.getElementById('bidAmount');
    if (!input) return;
    input.value = Number(input.value || 0) + Number(delta);
  }

  function placeBid() {
    if (currentLotId == null) return;
    const input = document.getElementById('bidAmount');
    const amount = parseFloat(input && input.value);
    const result = AgriAuction.placeBid(currentLotId, amount);
    const msg = document.getElementById('bidMsg');
    if (!result.ok) {
      if (msg) msg.innerHTML = `<div class="alert alert-danger">${result.error}</div>`;
      if (typeof uiOptions.onToast === 'function') uiOptions.onToast(result.error, true);
      return;
    }
    if (result.extended && typeof uiOptions.onToast === 'function') {
      uiOptions.onToast('Lot extended by 5 minutes (anti-sniping)');
    }
    if (typeof uiOptions.onToast === 'function') {
      uiOptions.onToast(`Bid of ${AgriAuction.formatMoney(amount)} placed`);
    }
    openLot(currentLotId);
    if (typeof uiOptions.onChange === 'function') uiOptions.onChange();
  }

  function toggleWatch(event, auctionId) {
    if (event) event.stopPropagation();
    const result = AgriAuction.toggleWatch(auctionId);
    if (typeof uiOptions.onToast === 'function') {
      uiOptions.onToast(result.watched ? 'Added to watchlist' : 'Removed from watchlist', !result.ok);
    }
    if (typeof uiOptions.onChange === 'function') uiOptions.onChange();
  }

  function configure(options) {
    uiOptions = Object.assign(uiOptions, options || {});
    startTick();
  }

  global.AgriAuctionUI = {
    card,
    renderGrid,
    openLot,
    closeLot,
    bumpBid,
    placeBid,
    toggleWatch,
    configure,
    currentLotId: function () { return currentLotId; },
  };
})(window);
