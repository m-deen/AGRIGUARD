/**
 * Frontend-only auction marketplace store.
 * Listings, bids, and watchlists persist in localStorage until the backend is wired up.
 */
(function (global) {
  const STORE_KEY = 'agriguard_auction_store';
  const STORE_VERSION = 2;
  const MIN_START_PRICE = 500;
  const MIN_INCREMENT = 100;
  const ANTI_SNIPE_MS = 5 * 60 * 1000;

  const SPECIES_ICONS = {
    Cattle: '<svg class="ag-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="11" r="5"/><path d="M5 8l2 2M19 8l-2 2M8 18v2M16 18v2M9 7C7 5 5 5 4 6M15 7c2-2 4-2 5-1"/></svg>',
    Goat: '<svg class="ag-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M12 14c-2.5 0-4-1.5-4-3.5S10 7 12 7s4 1.5 4 3.5S14.5 14 12 14z"/><path d="M8 8L5 4M16 8l3-4M10 18v2M14 18v2"/></svg>',
    Sheep: '<svg class="ag-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="4"/><circle cx="7" cy="11" r="2"/><circle cx="17" cy="11" r="2"/><circle cx="9" cy="16" r="2"/><circle cx="15" cy="16" r="2"/><path d="M10 8V6M14 8V6"/></svg>',
  };

  function nowIso() {
    return new Date().toISOString();
  }

  function daysFromNow(days) {
    return new Date(Date.now() + days * 24 * 60 * 60 * 1000).toISOString();
  }

  function hoursFromNow(hours) {
    return new Date(Date.now() + hours * 60 * 60 * 1000).toISOString();
  }

  function currentUser() {
    try {
      const raw = localStorage.getItem('agriguard_user');
      return raw ? JSON.parse(raw) : null;
    } catch (_) {
      return null;
    }
  }

  function userKey() {
    const user = currentUser();
    if (!user) return 'guest';
    return String(user.user_id || user.id || user.email || 'guest');
  }

  function userName() {
    const user = currentUser();
    if (!user) return 'You';
    return user.first_name || user.full_name || user.email || 'You';
  }

  function userRole() {
    const user = currentUser();
    const role = (user && user.role) || localStorage.getItem('agriguard_role') || '';
    return String(role).toLowerCase();
  }

  function seedState() {
    const animals = [
      { animal_id: 'a1', name: 'Themba', breed: 'Nguni', species: 'Cattle', ear_tag_id: 'AG-001', birth_date: '2020-03-15' },
      { animal_id: 'a2', name: 'Nala', breed: 'Bonsmara', species: 'Cattle', ear_tag_id: 'AG-002', birth_date: '2021-06-20' },
      { animal_id: 'a3', name: 'Simba', breed: 'Boer Goat', species: 'Goat', ear_tag_id: 'AG-003', birth_date: '2022-01-10' },
      { animal_id: 'a4', name: 'Bella', breed: 'Dorper', species: 'Sheep', ear_tag_id: 'AG-004', birth_date: '2023-04-02' },
      { animal_id: 'a5', name: 'Khaya', breed: 'Afrikaner', species: 'Cattle', ear_tag_id: 'AG-005', birth_date: '2021-11-08' },
      { animal_id: 'a6', name: 'Lucky', breed: 'Merino', species: 'Sheep', ear_tag_id: 'AG-006', birth_date: '2023-08-18' },
      { animal_id: 'x1', name: 'Kgosi', breed: 'Afrikaner', species: 'Cattle', ear_tag_id: 'AG-101', birth_date: '2019-02-01' },
      { animal_id: 'x2', name: 'Woolly', breed: 'Merino', species: 'Sheep', ear_tag_id: 'AG-102', birth_date: '2022-06-01' },
      { animal_id: 'x3', name: 'Thandi', breed: 'Nguni', species: 'Cattle', ear_tag_id: 'AG-103', birth_date: '2021-09-12' },
      { animal_id: 'x4', name: 'Pepper', breed: 'Boer Goat', species: 'Goat', ear_tag_id: 'AG-104', birth_date: '2023-01-20' },
    ];

    const auctions = [
      {
        auction_id: 1,
        animal_id: 'a1',
        title: '2022 NGUNI BULL — 4 YRS',
        species: 'Cattle',
        breed: 'Nguni',
        starting_price: 8000,
        current_bid: 8500,
        bid_count: 3,
        auction_end: daysFromNow(2),
        predicted_price: 9500,
        status: 'active',
        description: 'Registered Nguni bull, calm temperament, good fertility record.',
        seller_key: 'demo-farmer',
        seller_name: 'AgriGuard Demo Farm',
        yard: 'Bethlehem Yard',
        location: 'Free State',
        lot_no: 'AG-2401',
        created_at: nowIso(),
      },
      {
        auction_id: 2,
        animal_id: 'a2',
        title: '2023 BONSMARA COW — 3 YRS',
        species: 'Cattle',
        breed: 'Bonsmara',
        starting_price: 7500,
        current_bid: 7800,
        bid_count: 2,
        auction_end: daysFromNow(5),
        predicted_price: 8500,
        status: 'active',
        description: 'Proven breeder, good condition, vaccinated to date.',
        seller_key: 'demo-farmer',
        seller_name: 'Highveld Livestock',
        yard: 'Standerton Yard',
        location: 'Mpumalanga',
        lot_no: 'AG-2402',
        created_at: nowIso(),
      },
      {
        auction_id: 3,
        animal_id: 'a3',
        title: '2024 BOER GOAT BUCK',
        species: 'Goat',
        breed: 'Boer Goat',
        starting_price: 3000,
        current_bid: 3200,
        bid_count: 1,
        auction_end: hoursFromNow(18),
        predicted_price: 3600,
        status: 'active',
        description: 'Strong frame, ready for breeding this season.',
        seller_key: 'demo-farmer',
        seller_name: 'Karoo Fold',
        yard: 'Graaff-Reinet Yard',
        location: 'Eastern Cape',
        lot_no: 'AG-2403',
        created_at: nowIso(),
      },
      {
        auction_id: 4,
        animal_id: 'a4',
        title: '2024 DORPER EWE',
        species: 'Sheep',
        breed: 'Dorper',
        starting_price: 2100,
        current_bid: 2100,
        bid_count: 0,
        auction_end: daysFromNow(3),
        predicted_price: 2500,
        status: 'active',
        description: 'Young Dorper ewe, first listing, clean health record.',
        seller_key: 'demo-farmer',
        seller_name: 'AgriGuard Demo Farm',
        yard: 'Bethlehem Yard',
        location: 'Free State',
        lot_no: 'AG-2404',
        created_at: nowIso(),
      },
      {
        auction_id: 5,
        animal_id: 'x1',
        title: '2019 AFRIKANER OX — 7 YRS',
        species: 'Cattle',
        breed: 'Afrikaner',
        starting_price: 6200,
        current_bid: 7100,
        bid_count: 4,
        auction_end: hoursFromNow(4),
        predicted_price: 7800,
        status: 'active',
        description: 'Hardy Afrikaner ox, well adapted to dryland grazing.',
        seller_key: 'demo-farmer',
        seller_name: 'Kalahari Herd',
        yard: 'Upington Yard',
        location: 'Northern Cape',
        lot_no: 'AG-2405',
        created_at: nowIso(),
      },
      {
        auction_id: 6,
        animal_id: 'x2',
        title: '2022 MERINO RAM',
        species: 'Sheep',
        breed: 'Merino',
        starting_price: 2800,
        current_bid: 3050,
        bid_count: 2,
        auction_end: daysFromNow(4),
        predicted_price: 3400,
        status: 'active',
        description: 'Fine-wool Merino ram, strong conformation.',
        seller_key: 'demo-farmer',
        seller_name: 'Overberg Wool',
        yard: 'Caledon Yard',
        location: 'Western Cape',
        lot_no: 'AG-2406',
        created_at: nowIso(),
      },
      {
        auction_id: 7,
        animal_id: 'x3',
        title: '2021 NGUNI HEIFER — 5 YRS',
        species: 'Cattle',
        breed: 'Nguni',
        starting_price: 5400,
        current_bid: 5400,
        bid_count: 0,
        auction_end: daysFromNow(6),
        predicted_price: 6400,
        status: 'active',
        description: 'Nguni heifer, tick-resistant bloodline, ready to join the herd.',
        seller_key: 'demo-farmer',
        seller_name: 'Zululand Cattle',
        yard: 'Ulundi Yard',
        location: 'KwaZulu-Natal',
        lot_no: 'AG-2407',
        created_at: nowIso(),
      },
      {
        auction_id: 8,
        animal_id: 'x4',
        title: '2023 BOER GOAT DOE',
        species: 'Goat',
        breed: 'Boer Goat',
        starting_price: 2400,
        current_bid: 2550,
        bid_count: 1,
        auction_end: daysFromNow(2),
        predicted_price: 2900,
        status: 'active',
        description: 'Young Boer doe, good mothering line.',
        seller_key: 'demo-farmer',
        seller_name: 'Karoo Fold',
        yard: 'Graaff-Reinet Yard',
        location: 'Eastern Cape',
        lot_no: 'AG-2408',
        created_at: nowIso(),
      },
    ];

    const hour = 60 * 60 * 1000;
    const bids = [
      { bid_id: 1, auction_id: 1, buyer_key: 'peter-m', buyer_name: 'Peter M.', amount: 8200, time: new Date(Date.now() - 2 * hour).toISOString() },
      { bid_id: 2, auction_id: 1, buyer_key: 'john-d', buyer_name: 'John D.', amount: 8500, time: new Date(Date.now() - hour).toISOString() },
      { bid_id: 3, auction_id: 1, buyer_key: 'sarah-b', buyer_name: 'Sarah B.', amount: 8100, time: new Date(Date.now() - 3 * hour).toISOString() },
      { bid_id: 4, auction_id: 2, buyer_key: 'sarah-b', buyer_name: 'Sarah B.', amount: 7800, time: new Date(Date.now() - 0.5 * hour).toISOString() },
      { bid_id: 5, auction_id: 2, buyer_key: 'john-d', buyer_name: 'John D.', amount: 7600, time: new Date(Date.now() - 4 * hour).toISOString() },
      { bid_id: 6, auction_id: 3, buyer_key: 'peter-m', buyer_name: 'Peter M.', amount: 3200, time: new Date(Date.now() - hour).toISOString() },
      { bid_id: 7, auction_id: 5, buyer_key: 'john-d', buyer_name: 'John D.', amount: 7100, time: new Date(Date.now() - 0.3 * hour).toISOString() },
      { bid_id: 8, auction_id: 5, buyer_key: 'sarah-b', buyer_name: 'Sarah B.', amount: 6800, time: new Date(Date.now() - 2 * hour).toISOString() },
      { bid_id: 9, auction_id: 6, buyer_key: 'peter-m', buyer_name: 'Peter M.', amount: 3050, time: new Date(Date.now() - hour).toISOString() },
      { bid_id: 10, auction_id: 8, buyer_key: 'sarah-b', buyer_name: 'Sarah B.', amount: 2550, time: new Date(Date.now() - 1.5 * hour).toISOString() },
    ];

    return {
      version: STORE_VERSION,
      nextAuctionId: 9,
      nextBidId: 11,
      animals,
      auctions,
      bids,
      watch: {},
    };
  }

  function load() {
    try {
      const raw = localStorage.getItem(STORE_KEY);
      if (!raw) {
        const seeded = seedState();
        save(seeded);
        return seeded;
      }
      const data = JSON.parse(raw);
      if (!data || data.version !== STORE_VERSION || !Array.isArray(data.auctions)) {
        const seeded = seedState();
        save(seeded);
        return seeded;
      }
      data.animals = data.animals || [];
      data.auctions = data.auctions || [];
      data.bids = data.bids || [];
      data.watch = data.watch || {};
      return data;
    } catch (_) {
      const seeded = seedState();
      save(seeded);
      return seeded;
    }
  }

  function save(data) {
    localStorage.setItem(STORE_KEY, JSON.stringify(data));
  }

  function withState(mutator) {
    const state = load();
    const result = mutator(state);
    save(state);
    return result;
  }

  function isEnded(auction) {
    if (!auction) return true;
    if (auction.status === 'ended') return true;
    return new Date(auction.auction_end).getTime() <= Date.now();
  }

  function decorateAuction(auction) {
    const ended = isEnded(auction);
    return Object.assign({}, auction, {
      listing_id: auction.auction_id,
      end_time: auction.auction_end,
      status: ended ? 'ended' : 'active',
      mine: String(auction.seller_key) === userKey(),
    });
  }

  function listAuctions(filters) {
    const opts = filters || {};
    const search = String(opts.search || '').toLowerCase();
    const species = opts.species || '';
    const status = opts.status || 'active';

    return load().auctions
      .map(decorateAuction)
      .filter((auction) => {
        if (status === 'active' && auction.status !== 'active') return false;
        if (status === 'ended' && auction.status !== 'ended') return false;
        if (species && auction.species !== species) return false;
        if (search) {
          const hay = `${auction.title} ${auction.breed} ${auction.species} ${auction.description || ''}`.toLowerCase();
          if (!hay.includes(search)) return false;
        }
        return true;
      })
      .sort((a, b) => new Date(a.auction_end) - new Date(b.auction_end));
  }

  function getAuction(auctionId) {
    const id = Number(auctionId);
    const found = load().auctions.find((auction) => Number(auction.auction_id) === id);
    return found ? decorateAuction(found) : null;
  }

  function getAnimals() {
    const listed = new Set(
      load().auctions
        .filter((auction) => !isEnded(auction))
        .map((auction) => String(auction.animal_id))
    );
    return load().animals.map((animal) => Object.assign({}, animal, {
      listed: listed.has(String(animal.animal_id)),
    }));
  }

  function getBids(auctionId) {
    const id = Number(auctionId);
    return load().bids
      .filter((bid) => Number(bid.auction_id) === id)
      .sort((a, b) => b.amount - a.amount || new Date(b.time) - new Date(a.time))
      .map((bid) => Object.assign({}, bid, {
        listing_id: bid.auction_id,
        buyer: bid.buyer_name,
      }));
  }

  function minBidFor(auction) {
    const current = Number(auction.current_bid || auction.starting_price || 0);
    if (!auction.bid_count) {
      return Math.max(current, MIN_START_PRICE);
    }
    return current + MIN_INCREMENT;
  }

  function createListing(input) {
    const animalId = input && input.animal_id;
    const price = Number(input && input.starting_price);
    const durationDays = Number((input && input.duration_days) || 3);
    const description = (input && input.description) || '';

    if (!animalId) return { ok: false, error: 'Select an animal' };
    if (!price || price < MIN_START_PRICE) {
      return { ok: false, error: `Set a starting price of at least R${MIN_START_PRICE}` };
    }

    return withState((state) => {
      const animal = state.animals.find((item) => String(item.animal_id) === String(animalId));
      if (!animal) return { ok: false, error: 'Animal not found' };

      const alreadyListed = state.auctions.some(
        (auction) => String(auction.animal_id) === String(animalId) && !isEnded(auction)
      );
      if (alreadyListed) {
        return { ok: false, error: `${animal.name} is already listed in an active auction` };
      }

      const age = Math.max(0, Math.floor((Date.now() - new Date(animal.birth_date).getTime()) / (365.25 * 24 * 3600 * 1000)));
      const year = new Date(animal.birth_date).getFullYear();
      const auctionId = state.nextAuctionId++;
      const auction = {
        auction_id: auctionId,
        animal_id: animal.animal_id,
        title: `${year} ${String(animal.breed).toUpperCase()} ${String(animal.species).toUpperCase()} — ${age} YRS`,
        species: animal.species,
        breed: animal.breed,
        starting_price: price,
        current_bid: price,
        bid_count: 0,
        auction_end: daysFromNow(durationDays),
        predicted_price: Math.round(price * 1.2),
        status: 'active',
        description,
        seller_key: userKey(),
        seller_name: userName(),
        yard: (input && input.yard) || `${userName()} Yard`,
        location: (input && input.location) || 'South Africa',
        lot_no: 'AG-' + String(2400 + auctionId),
        created_at: nowIso(),
      };
      state.auctions.unshift(auction);
      return { ok: true, auction: decorateAuction(auction) };
    });
  }

  function placeBid(auctionId, amount) {
    const bidAmount = Number(amount);
    if (!bidAmount || bidAmount <= 0) {
      return { ok: false, error: 'Enter a valid bid amount' };
    }

    return withState((state) => {
      const auction = state.auctions.find((item) => Number(item.auction_id) === Number(auctionId));
      if (!auction) return { ok: false, error: 'Auction not found' };
      if (isEnded(auction)) return { ok: false, error: 'This auction has ended' };
      if (String(auction.seller_key) === userKey()) {
        return { ok: false, error: 'You cannot bid on your own listing' };
      }

      const minimum = minBidFor(decorateAuction(auction));
      if (bidAmount < minimum) {
        return { ok: false, error: `Minimum bid is R${minimum.toLocaleString()}` };
      }

      let extended = false;
      const remaining = new Date(auction.auction_end).getTime() - Date.now();
      if (remaining > 0 && remaining < ANTI_SNIPE_MS) {
        auction.auction_end = new Date(new Date(auction.auction_end).getTime() + ANTI_SNIPE_MS).toISOString();
        extended = true;
      }

      const bid = {
        bid_id: state.nextBidId++,
        auction_id: auction.auction_id,
        buyer_key: userKey(),
        buyer_name: userName(),
        amount: bidAmount,
        time: nowIso(),
      };
      state.bids.push(bid);
      auction.current_bid = bidAmount;
      auction.bid_count = (auction.bid_count || 0) + 1;

      return { ok: true, extended, bid, auction: decorateAuction(auction) };
    });
  }

  function bidStatusForAuction(auction, userMaxBid) {
    const ended = isEnded(auction);
    const winning = userMaxBid >= Number(auction.current_bid || 0);
    if (!ended && winning) return 'active';
    if (!ended && !winning) return 'outbid';
    if (ended && winning) return 'won';
    return 'lost';
  }

  function myBids() {
    const key = userKey();
    const state = load();
    const mine = state.bids.filter((bid) => bid.buyer_key === key);
    const byAuction = {};
    mine.forEach((bid) => {
      const id = Number(bid.auction_id);
      if (!byAuction[id] || bid.amount > byAuction[id].amount) {
        byAuction[id] = bid;
      }
    });

    return Object.values(byAuction)
      .map((bid) => {
        const auction = state.auctions.find((item) => Number(item.auction_id) === Number(bid.auction_id));
        if (!auction) return null;
        const decorated = decorateAuction(auction);
        return {
          id: bid.bid_id,
          auction_id: decorated.auction_id,
          auction_title: decorated.title,
          species: decorated.species,
          amount: bid.amount,
          bid_date: bid.time.slice(0, 10),
          bid_time: bid.time,
          status: bidStatusForAuction(decorated, bid.amount),
          current_bid: decorated.current_bid,
          auction_end: decorated.auction_end,
        };
      })
      .filter(Boolean)
      .sort((a, b) => new Date(b.bid_time) - new Date(a.bid_time));
  }

  function endingSoon(limit) {
    return listAuctions({ status: 'active' }).slice(0, limit || 5);
  }

  function watchlist() {
    const state = load();
    const entries = state.watch[userKey()] || [];
    return entries
      .map((entry) => {
        const auction = state.auctions.find((item) => Number(item.auction_id) === Number(entry.auction_id));
        if (!auction) return null;
        const decorated = decorateAuction(auction);
        return Object.assign({}, decorated, {
          id: decorated.auction_id,
          added_date: (entry.added_date || '').slice(0, 10),
        });
      })
      .filter(Boolean);
  }

  function isWatched(auctionId) {
    const entries = load().watch[userKey()] || [];
    return entries.some((entry) => Number(entry.auction_id) === Number(auctionId));
  }

  function toggleWatch(auctionId) {
    return withState((state) => {
      const id = Number(auctionId);
      const auction = state.auctions.find((item) => Number(item.auction_id) === id);
      if (!auction) return { ok: false, error: 'Auction not found', watched: false };

      const key = userKey();
      const entries = state.watch[key] || [];
      const index = entries.findIndex((entry) => Number(entry.auction_id) === id);
      if (index >= 0) {
        entries.splice(index, 1);
        state.watch[key] = entries;
        return { ok: true, watched: false };
      }
      entries.push({ auction_id: id, added_date: nowIso() });
      state.watch[key] = entries;
      return { ok: true, watched: true };
    });
  }

  function removeWatch(auctionId) {
    return withState((state) => {
      const key = userKey();
      state.watch[key] = (state.watch[key] || []).filter(
        (entry) => Number(entry.auction_id) !== Number(auctionId)
      );
      return { ok: true };
    });
  }

  function clearWatch() {
    return withState((state) => {
      state.watch[userKey()] = [];
      return { ok: true };
    });
  }

  function stats() {
    const active = listAuctions({ status: 'active' });
    const bids = myBids();
    const watch = watchlist();
    const won = bids.filter((bid) => bid.status === 'won');
    return {
      activeAuctions: active.length,
      myActiveBids: bids.filter((bid) => bid.status === 'active').length,
      watchCount: watch.length,
      totalBids: bids.length,
      winningBids: won.length,
      lostBids: bids.filter((bid) => bid.status === 'lost' || bid.status === 'outbid').length,
      totalSpent: won.reduce((sum, bid) => sum + Number(bid.amount || 0), 0),
    };
  }

  function formatTimeLeft(endTime) {
    const diff = new Date(endTime).getTime() - Date.now();
    if (diff <= 0 || !endTime) return 'Ended';
    const days = Math.floor(diff / (1000 * 60 * 60 * 24));
    const hours = Math.floor((diff % 86400000) / 3600000);
    const minutes = Math.floor((diff % 3600000) / 60000);
    if (days > 0) return `${days}d ${hours}h`;
    if (hours > 0) return `${hours}h ${minutes}m`;
    return `${minutes}m`;
  }

  function formatCountdown(endTime) {
    const diff = new Date(endTime).getTime() - Date.now();
    if (diff <= 0 || !endTime) return '0d 0h 0m 0s';
    const days = Math.floor(diff / 86400000);
    const hours = Math.floor((diff % 86400000) / 3600000);
    const minutes = Math.floor((diff % 3600000) / 60000);
    const seconds = Math.floor((diff % 60000) / 1000);
    return `${days}d ${hours}h ${minutes}m ${seconds}s`;
  }

  function formatExpires(endTime) {
    const date = new Date(endTime);
    if (Number.isNaN(date.getTime())) return '';
    return date.toLocaleString('en-GB', {
      weekday: 'short',
      day: 'numeric',
      month: 'short',
      year: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
  }

  function formatMoney(amount) {
    return 'R' + Number(amount || 0).toLocaleString('en-ZA');
  }

  function bidLabel(auction) {
    return Number(auction && auction.bid_count) > 0 ? 'Current Bid' : 'Starting Bid';
  }

  function lotTags(auction) {
    const tags = [];
    if (auction.species) tags.push(auction.species);
    if (auction.breed) tags.push(auction.breed);
    if (Number(auction.bid_count) >= 3) tags.push('Hot Interest');
    if (!auction.bid_count) tags.push('Opening');
    if (auction.mine) tags.push('Your listing');
    return tags;
  }

  function speciesIcon(species) {
    return SPECIES_ICONS[species] || '<svg class="ag-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="12" cy="12" r="9"/></svg>';
  }

  global.AgriAuction = {
    MIN_START_PRICE,
    MIN_INCREMENT,
    currentUser,
    userKey,
    userName,
    userRole,
    listAuctions,
    getAuction,
    getAnimals,
    getBids,
    minBidFor,
    createListing,
    placeBid,
    myBids,
    endingSoon,
    watchlist,
    isWatched,
    toggleWatch,
    removeWatch,
    clearWatch,
    stats,
    formatTimeLeft,
    formatCountdown,
    formatExpires,
    formatMoney,
    bidLabel,
    lotTags,
    speciesIcon,
  };
})(window);
