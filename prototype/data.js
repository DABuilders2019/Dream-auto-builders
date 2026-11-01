/* Dream Auto Builders – prototype catalog.
   Shaped like the data the live site will import:
   - PIES (product info): brand, part number, title, price, features, specs, images
   - ACES (fitment): year range + make + model + submodel + notes per product
   - Distributor stock: Turn14 / Keystone quantities
   Everything below is SAMPLE data: brands, part numbers and prices are placeholders. */
window.DAB = (function () {
  'use strict';

  const BRANDS = [
    { id: 'apex', name: 'Apex Lift Co.', prefix: 'APX' },
    { id: 'monarch', name: 'Monarch Suspension', prefix: 'MSU' },
    { id: 'ridgeline', name: 'Ridgeline Off-Road', prefix: 'RDG' },
    { id: 'ironclad', name: 'Ironclad Exhaust', prefix: 'ICX' },
    { id: 'vortex', name: 'VortexFlow', prefix: 'VFX' },
    { id: 'lumen', name: 'Lumen Forge', prefix: 'LMF' },
    { id: 'trailguard', name: 'TrailGuard', prefix: 'TGD' },
    { id: 'cabin', name: 'Cabin Armor', prefix: 'CAR' },
    { id: 'torque', name: 'Torque Theory', prefix: 'TQT' },
    { id: 'volt', name: 'Volt Ignition', prefix: 'VLT' },
    { id: 'halo', name: 'Halo Brake Systems', prefix: 'HBS' },
    { id: 'gridiron', name: 'Grid Iron Wheels', prefix: 'GIW' },
    { id: 'roadgrip', name: 'RoadGrip Tires', prefix: 'RGT' },
    { id: 'diode', name: 'Diode Dynamics', prefix: 'DD', real: true },
    { id: 'magnaflow', name: 'MagnaFlow', prefix: 'MF', real: true },
    { id: 'mickey', name: 'Mickey Thompson', prefix: 'MT', real: true },
    { id: 'autometer', name: 'AutoMeter', prefix: 'AM', real: true },
    { id: 'readylift', name: 'ReadyLift', prefix: 'RL', real: true }
  ];

  const CATS = [
    { id: 'suspension', img: 'img/c-suspension.jpg', name: 'Suspension', icon: 'spring', subs: [['lift-kits', 'Lift Kits'], ['leveling-kits', 'Leveling Kits'], ['lift-blocks', 'Lift Blocks'], ['suspension-components', 'Suspension Components'], ['shocks', 'Shocks & Struts'], ['coilovers', 'Coilovers'], ['lowering-springs', 'Lowering Springs']] },
    { id: 'exhaust', img: 'img/p-exhaust.jpg', name: 'Exhaust', icon: 'exhaust', subs: [['cat-back', 'Cat-Back Systems'], ['axle-back', 'Axle-Back Systems'], ['mufflers', 'Mufflers'], ['catalytic-converters', 'Catalytic Converters'], ['exhaust-tips', 'Exhaust Tips'], ['exhaust-pipes', 'Pipes, Clamps & Hardware'], ['exhaust-kits', 'Other Exhaust Kits'], ['headers', 'Headers']] },
    { id: 'intake', img: 'img/c-intake.jpg', name: 'Air Intake', icon: 'intake', subs: [['cold-air-intakes', 'Cold Air Intakes'], ['air-filters', 'Air Filters']] },
    { id: 'lighting', img: 'img/c-lighting.jpg', name: 'Lighting', icon: 'light', subs: [['headlights', 'Headlights'], ['light-bars', 'LED Light Bars'], ['tail-lights', 'Tail Lights']] },
    { id: 'tires', img: 'img/c-tires.jpg', name: 'Tires', icon: 'tire', subs: [['all-terrain', 'All-Terrain Tires'], ['mud-terrain', 'Mud-Terrain Tires'], ['performance-tires', 'Performance Tires'], ['all-season', 'All-Season Tires']] },
    { id: 'wheels', img: 'img/p-wheel.jpg', name: 'Wheels', icon: 'wheel', subs: [['wheels', 'Wheels'], ['wheel-spacers', 'Wheel Spacers']] },
    { id: 'exterior', img: 'img/c-exterior.jpg', name: 'Exterior', icon: 'truck', subs: [['running-boards', 'Running Boards & Steps'], ['bumpers', 'Bumpers'], ['fender-flares', 'Fender Flares'], ['tonneau', 'Tonneau Covers'], ['recovery', 'Recovery Gear']] },
    { id: 'interior', img: 'img/c-interior.jpg', name: 'Interior', icon: 'seat', subs: [['floor-liners', 'Floor Liners'], ['seat-covers', 'Seat Covers']] },
    { id: 'performance', img: 'img/c-performance.jpg', name: 'Performance', icon: 'gauge', subs: [['tuners', 'Tuners & Programmers'], ['gauges', 'Gauges & Displays'], ['ignition', 'Spark Plugs & Ignition']] },
    { id: 'brakes', img: 'img/c-brakes.jpg', name: 'Brakes', icon: 'brake', subs: [['big-brake-kits', 'Big Brake Kits'], ['pads-rotors', 'Pads & Rotors']] }
  ];

  // Vehicle platforms (in the live site these come from the ACES vehicle database)
  const PLAT = {
    f150: { tire: '275/65R18', make: 'Ford', model: 'F-150', from: 2015, to: 2020, sub: 'XL, XLT, Lariat, King Ranch, Platinum', bolt: '6x135', engine: '2.7L / 3.5L V6, 5.0L V8' },
    f150b: { tire: '275/65R18', make: 'Ford', model: 'F-150', from: 2021, to: 2024, sub: 'XL, XLT, Lariat, Tremor', bolt: '6x135', engine: '2.7L / 3.5L V6, 5.0L V8' },
    silv: { tire: '275/60R20', make: 'Chevrolet', model: 'Silverado 1500', from: 2019, to: 2024, sub: 'WT, Custom, LT, RST, LTZ, High Country', bolt: '6x139.7', engine: '2.7L I4, 5.3L / 6.2L V8' },
    sierra: { tire: '275/60R20', make: 'GMC', model: 'Sierra 1500', from: 2019, to: 2024, sub: 'SLE, Elevation, SLT, AT4, Denali', bolt: '6x139.7', engine: '2.7L I4, 5.3L / 6.2L V8' },
    ram: { tire: '275/55R20', make: 'Ram', model: '1500', from: 2019, to: 2024, sub: 'Tradesman, Big Horn, Laramie, Rebel, Limited', bolt: '6x139.7', engine: '3.6L V6, 5.7L V8', note: 'Excludes Ram 1500 Classic' },
    tacoma: { tire: '265/70R16', make: 'Toyota', model: 'Tacoma', from: 2016, to: 2023, sub: 'SR, SR5, TRD Sport, TRD Off-Road, Limited', bolt: '6x139.7', engine: '2.7L I4, 3.5L V6' },
    fourrunner: { tire: '265/70R17', make: 'Toyota', model: '4Runner', from: 2010, to: 2024, sub: 'SR5, TRD Off-Road, Limited, TRD Pro', bolt: '6x139.7', engine: '4.0L V6' },
    jl: { tire: '255/75R17', make: 'Jeep', model: 'Wrangler', from: 2018, to: 2024, sub: 'Sport, Sahara, Rubicon (JL)', bolt: '5x127', engine: '2.0L I4 Turbo, 3.6L V6', note: 'JL body style only' },
    jk: { tire: '255/75R17', make: 'Jeep', model: 'Wrangler', from: 2007, to: 2018, sub: 'Sport, Sahara, Rubicon (JK)', bolt: '5x127', engine: '3.6L / 3.8L V6', note: 'JK body style only' },
    bronco: { tire: '255/75R17', make: 'Ford', model: 'Bronco', from: 2021, to: 2024, sub: 'Base, Big Bend, Badlands, Wildtrak', bolt: '6x139.7', engine: '2.3L I4, 2.7L V6' },
    mustang: { tire: '255/40R19', make: 'Ford', model: 'Mustang', from: 2015, to: 2023, sub: 'EcoBoost, GT', bolt: '5x114.3', engine: '2.3L I4 Turbo, 5.0L V8' },
    camaro: { tire: '245/40R20', make: 'Chevrolet', model: 'Camaro', from: 2016, to: 2024, sub: 'LT, SS, ZL1', bolt: '5x120', engine: '2.0L I4 Turbo, 3.6L V6, 6.2L V8' },
    chall: { tire: '245/45R20', make: 'Dodge', model: 'Challenger', from: 2015, to: 2023, sub: 'SXT, GT, R/T, Scat Pack', bolt: '5x115', engine: '3.6L V6, 5.7L / 6.4L V8' },
    charger: { tire: '245/45R20', make: 'Dodge', model: 'Charger', from: 2015, to: 2023, sub: 'SXT, GT, R/T, Scat Pack', bolt: '5x115', engine: '3.6L V6, 5.7L / 6.4L V8' },
    wrx: { tire: '245/40R18', make: 'Subaru', model: 'WRX', from: 2015, to: 2021, sub: 'Base, Premium, Limited, STI', bolt: '5x114.3', engine: '2.0L H4 Turbo, 2.5L H4 Turbo' },
    civic: { tire: '235/40R18', make: 'Honda', model: 'Civic', from: 2016, to: 2021, sub: 'LX, Sport, EX, Si', bolt: '5x114.3', engine: '1.5L I4 Turbo, 2.0L I4' }
  };

  const TRUCKS = ['f150', 'f150b', 'silv', 'sierra', 'ram', 'tacoma', 'fourrunner'];
  const FULLSIZE = ['f150', 'f150b', 'silv', 'sierra', 'ram'];
  const JEEPS = ['jl', 'jk', 'bronco'];
  const MUSCLE = ['mustang', 'camaro', 'chall', 'charger'];
  const SPORT = ['wrx', 'civic'];
  const ALL = Object.keys(PLAT);

  const CARB = 'Not legal for sale or use in California on pollution-controlled motor vehicles.';

  // Product templates → one product per vehicle platform (or one universal product)
  const T = [
    { key: 'lev', brand: 'apex', cat: 'suspension', sub: 'leveling-kits', name: '2.5" Front Leveling Kit', base: 119, spread: 90, weight: 8, plats: TRUCKS.concat(['bronco']),
      features: ['Raises the front 2.5" to level the factory rake', 'Clears up to 33" tires with no trimming', 'Retains factory ride quality and alignment range', 'Bolt-on install in about 2 hours with basic hand tools'],
      specs: { 'Lift Height': '2.5 in. front', 'Material': 'Billet aluminum spacers', 'Finish': 'Black anodized', 'Max Tire Size': '33 in.', 'Install Time': '2 hours', 'Warranty': 'Lifetime limited' }, kw: 'level lift spacer' },
    { key: 'lift6', brand: 'apex', cat: 'suspension', sub: 'lift-kits', name: '6" Suspension Lift Kit with Shocks', base: 1849, spread: 650, weight: 210, plats: FULLSIZE,
      features: ['6" of lift for up to 35" tires', 'Includes front knuckles, crossmembers and rear blocks', 'Nitrogen-charged rear shocks included', 'Retains factory 4WD and stability systems'],
      specs: { 'Lift Height': '6 in.', 'Max Tire Size': '35 in.', 'Shocks Included': 'Yes, rear', 'Material': 'Steel', 'Finish': 'Black powder coat', 'Install Time': '8 to 10 hours', 'Warranty': 'Lifetime limited' }, kw: 'lift kit raise', fitNote: '4WD only' },
    { key: 'lift35', brand: 'ridgeline', cat: 'suspension', sub: 'lift-kits', name: '3.5" Lift Kit with Coil Springs', base: 899, spread: 400, weight: 140, plats: JEEPS.concat(['tacoma', 'fourrunner']),
      features: ['3.5" coil spring lift for up to 37" tires', 'Progressive-rate coils for on- and off-road comfort', 'Adjustable track bars and extended brake lines', 'Bump stop extensions included'],
      specs: { 'Lift Height': '3.5 in.', 'Max Tire Size': '37 in.', 'Spring Type': 'Progressive-rate coil', 'Finish': 'Red powder coat', 'Install Time': '6 hours', 'Warranty': '5 years' }, kw: 'lift kit coil spring' },
    { key: 'shock', brand: 'monarch', cat: 'suspension', sub: 'shocks', name: '2.5" Monotube Shocks (Set of 4)', base: 489, spread: 260, weight: 34, plats: TRUCKS.concat(JEEPS),
      features: ['2.5" monotube body runs cooler than twin-tube shocks', 'Valving tuned for 0 to 2.5" of lift', 'Zinc-plated body with polished piston rod', 'Direct bolt-in replacement'],
      specs: { 'Body Diameter': '2.5 in.', 'Shock Type': 'Monotube, nitrogen charged', 'Adjustable': 'No', 'Lift Range': '0 to 2.5 in.', 'Quantity': '4', 'Warranty': 'Lifetime limited' }, kw: 'shock absorber strut' },
    { key: 'coil', brand: 'monarch', cat: 'suspension', sub: 'coilovers', name: 'Street Series Coilovers', base: 1099, spread: 500, weight: 52, plats: MUSCLE.concat(SPORT),
      features: ['32-way damping adjustment', 'Ride height adjustable from -0.5" to -2.5"', 'Camber plates included on the front', 'Rebuildable and revalvable'],
      specs: { 'Adjustable Damping': '32-way', 'Drop Range': '0.5 to 2.5 in.', 'Spring Rate (F/R)': '10k / 8k', 'Camber Plates': 'Included (front)', 'Warranty': '2 years' }, kw: 'coilover lowering' },
    { key: 'lower', brand: 'monarch', cat: 'suspension', sub: 'lowering-springs', name: 'Sport Lowering Springs', base: 249, spread: 120, weight: 28, plats: MUSCLE.concat(SPORT),
      features: ['Lowers about 1.2" front and 1.0" rear', 'Progressive rate keeps a comfortable ride', 'Works with factory shocks', 'Powder coated for corrosion resistance'],
      specs: { 'Drop (F/R)': '1.2 in. / 1.0 in.', 'Spring Rate': 'Progressive', 'Quantity': '4', 'Finish': 'Red powder coat', 'Warranty': '1 million miles' }, kw: 'lowering springs drop' },
    { key: 'catback', brand: 'ironclad', cat: 'exhaust', sub: 'cat-back', name: 'Cat-Back Exhaust System', base: 899, spread: 500, weight: 48, plats: ['f150', 'f150b', 'silv', 'ram', 'tacoma', 'mustang', 'camaro', 'chall', 'charger', 'wrx', 'civic'],
      features: ['Mandrel-bent 304 stainless tubing', 'Aggressive tone with no drone at highway speed', 'Polished 4" tips', 'Bolt-on with factory hangers'],
      specs: { 'Material': '304 stainless steel', 'Pipe Diameter': '3 in.', 'Tip Finish': 'Polished', 'Tip Size': '4 in.', 'Sound Level': 'Aggressive', 'Emissions': '50-state legal (cat-back)', 'Warranty': 'Lifetime limited' }, kw: 'exhaust catback muffler' },
    { key: 'axle', brand: 'ironclad', cat: 'exhaust', sub: 'axle-back', name: 'Axle-Back Exhaust', base: 549, spread: 250, weight: 26, plats: MUSCLE,
      features: ['Replaces the factory mufflers only', 'Deep, throaty tone', 'Black ceramic-coated 4.5" tips', 'Installs in under an hour'],
      specs: { 'Material': '304 stainless steel', 'Tip Finish': 'Black ceramic', 'Tip Size': '4.5 in.', 'Sound Level': 'Moderate', 'Emissions': '50-state legal', 'Warranty': 'Lifetime limited' }, kw: 'axleback exhaust muffler' },
    { key: 'headers', brand: 'ironclad', cat: 'exhaust', sub: 'headers', name: '1-7/8" Long-Tube Headers', base: 1299, spread: 450, weight: 40, plats: ['mustang', 'camaro', 'chall'],
      features: ['1-7/8" primaries with 3" collectors', 'Ceramic coated to reduce under-hood heat', 'CNC-machined flanges for a leak-free seal', 'Off-road use only'],
      specs: { 'Primary Tube': '1-7/8 in.', 'Collector': '3 in.', 'Material': '304 stainless steel', 'Coating': 'Ceramic', 'Emissions': CARB, 'Warranty': '1 year' }, kw: 'headers long tube',
      fitNote: (p) => ({ mustang: 'GT, 5.0L V8 only', camaro: 'SS, 6.2L V8 only', chall: 'R/T, 5.7L V8 only' }[p]) },
    { key: 'cai', brand: 'vortex', cat: 'intake', sub: 'cold-air-intakes', name: 'Cold Air Intake System', base: 329, spread: 160, weight: 15, plats: ALL.filter((p) => p !== 'jk'),
      features: ['Rotomolded tube for smooth airflow', 'Oiled cotton filter washes and reuses', 'Heat shield seals against the hood', 'Up to 14 hp gain (dyno tested on sample vehicle)'],
      specs: { 'Filter Type': 'Oiled cotton, washable', 'Tube Material': 'Rotomolded polyethylene', 'Heat Shield': 'Included', 'Emissions': CARB, 'Warranty': '1 million miles' }, kw: 'cold air intake cai filter',
      fitNote: (p) => PLAT[p].engine.split(',')[0] + ' engine' },
    { key: 'filter', brand: 'vortex', cat: 'intake', sub: 'air-filters', name: 'Drop-In Replacement Air Filter', base: 54, spread: 30, weight: 1.5, plats: ALL,
      features: ['Direct replacement for the factory paper filter', 'Washable and reusable', 'More airflow than a disposable filter', 'Installs in minutes'],
      specs: { 'Filter Type': 'Oiled cotton, washable', 'Shape': 'Panel', 'Service Interval': '50,000 miles', 'Emissions': '50-state legal', 'Warranty': '1 million miles' }, kw: 'air filter panel' },
    { key: 'headlight', brand: 'lumen', cat: 'lighting', sub: 'headlights', name: 'Projector LED Headlights (Pair)', base: 549, spread: 400, weight: 18, plats: ['jl', 'jk', 'bronco', 'f150', 'tacoma', 'fourrunner', 'mustang', 'silv'],
      features: ['DOT/SAE-compliant projector beam', 'Sequential turn signals', 'Plug-and-play harness with no splicing', 'Sealed housing with anti-fog vents'],
      specs: { 'Light Source': 'LED projector', 'Color Temperature': '6000K', 'Housing': 'Black', 'Compliance': 'DOT / SAE', 'Quantity': 'Pair', 'Warranty': '2 years' }, kw: 'headlight led lights' },
    { key: 'tail', brand: 'lumen', cat: 'lighting', sub: 'tail-lights', name: 'Sequential LED Tail Lights', base: 389, spread: 220, weight: 12, plats: ['mustang', 'chall', 'f150', 'f150b', 'jl', 'silv'],
      features: ['Sequential turn signal animation', 'Smoked lens', 'Plug-and-play', 'Built-in resistors, so no hyper-flash'],
      specs: { 'Light Source': 'LED', 'Lens': 'Smoked', 'Compliance': 'DOT / SAE', 'Quantity': 'Pair', 'Warranty': '2 years' }, kw: 'tail lights taillights led' },
    { key: 'fender', brand: 'trailguard', cat: 'exterior', sub: 'fender-flares', name: 'Pocket-Style Fender Flares (4 pc)', base: 329, spread: 220, weight: 30, plats: ['jl', 'jk', 'tacoma', 'f150', 'silv', 'ram'],
      features: ['Covers up to 2" more tire', 'UV-stable textured TPO', 'Paintable surface', 'Uses factory mounting holes on most models'],
      specs: { 'Material': 'TPO', 'Finish': 'Textured black, paintable', 'Coverage': '2 in.', 'Pieces': '4', 'Drilling Required': 'No', 'Warranty': '3 years' }, kw: 'fender flares' },
    { key: 'board', brand: 'trailguard', cat: 'exterior', sub: 'running-boards', name: '6" Aluminum Running Boards', base: 379, spread: 200, weight: 42, plats: FULLSIZE.concat(['tacoma', 'fourrunner', 'bronco']),
      features: ['Extruded aluminum with full-length grip tread', '300 lb weight rating per side', 'Vehicle-specific brackets', 'No drilling required'],
      specs: { 'Material': 'Aluminum', 'Width': '6 in.', 'Finish': 'Black textured', 'Weight Capacity': '300 lb per side', 'Drilling Required': 'No', 'Warranty': 'Lifetime limited' }, kw: 'running boards nerf bars side steps step', fitNote: () => 'Crew Cab' },
    { key: 'tonneau', brand: 'trailguard', cat: 'exterior', sub: 'tonneau', name: 'Hard Tri-Fold Tonneau Cover', base: 649, spread: 300, weight: 50, plats: FULLSIZE.concat(['tacoma']),
      features: ['Aluminum-reinforced panels', 'Folds up in seconds, removes without tools', 'Supports up to 400 lb', 'Works with most bed rail systems'],
      specs: { 'Style': 'Hard tri-fold', 'Material': 'Aluminum panels', 'Weight Capacity': '400 lb', 'Locking': 'Yes, with tailgate', 'Warranty': '5 years' }, kw: 'tonneau bed cover truck',
      fitNote: (p) => (p === 'tacoma' ? '5 ft bed' : '5.5 ft bed') },
    { key: 'bumper', brand: 'ridgeline', cat: 'exterior', sub: 'bumpers', name: 'Winch-Ready Front Bumper', base: 1099, spread: 500, weight: 120, plats: ['jl', 'jk', 'bronco', 'tacoma', 'fourrunner'],
      features: ['3/16" plate steel', 'Fits up to a 12,000 lb winch', 'Two 3/4" D-ring mounts', 'Pre-cut for factory fog lights'],
      specs: { 'Material': '3/16 in. steel', 'Finish': 'Textured black powder coat', 'Winch Capacity': '12,000 lb', 'D-Ring Mounts': '2', 'Warranty': 'Lifetime limited' }, kw: 'front bumper winch' },
    { key: 'liner', brand: 'cabin', cat: 'interior', sub: 'floor-liners', name: 'Laser-Measured Floor Liners (Front & Rear)', base: 159, spread: 70, weight: 14, plats: ALL,
      features: ['Laser-measured for an exact fit', 'Raised edges hold water, mud and snow', 'Odorless, BPA-free TPE', 'Uses factory retention posts'],
      specs: { 'Material': 'TPE', 'Color': 'Black', 'Rows Covered': 'Front and 2nd row', 'Warranty': 'Lifetime limited' }, kw: 'floor mats liners' },
    { key: 'seat', brand: 'cabin', cat: 'interior', sub: 'seat-covers', name: 'Neoprene Front Seat Covers', base: 229, spread: 80, weight: 7, plats: TRUCKS.concat(JEEPS),
      features: ['Water-resistant neoprene', 'Airbag-compatible side seams', 'Red contrast stitching', 'Custom pattern for your seats'],
      specs: { 'Material': 'Neoprene', 'Color': 'Black with red stitching', 'Rows': 'Front buckets', 'Airbag Compatible': 'Yes', 'Warranty': '3 years' }, kw: 'seat covers' },
    { key: 'tuner', brand: 'torque', cat: 'performance', sub: 'tuners', name: 'Handheld Performance Tuner', base: 449, spread: 150, weight: 2, plats: ['f150', 'f150b', 'silv', 'ram', 'mustang', 'camaro', 'chall', 'charger', 'wrx'],
      features: ['Preloaded performance and towing tunes', 'Adjusts speedometer for larger tires', 'Reads and clears trouble codes', '5" color touchscreen'],
      specs: { 'Display': '5 in. color touchscreen', 'Preloaded Tunes': '87 / 91 / 93 octane, towing', 'Gauge Monitoring': 'Yes', 'Emissions': CARB, 'Warranty': '1 year' }, kw: 'tuner programmer tune' },
    { key: 'plugs', brand: 'volt', cat: 'performance', sub: 'ignition', name: 'Iridium Spark Plug Set', base: 49, spread: 60, weight: 1, plats: ALL,
      features: ['Fine-wire iridium tip', 'Pre-gapped for your engine', 'Up to 100,000 mile service life', 'Complete set for one engine'],
      specs: { 'Electrode': 'Iridium', 'Pre-Gapped': 'Yes', 'Service Life': '100,000 miles', 'Emissions': '50-state legal', 'Warranty': '1 year' }, kw: 'spark plugs ignition',
      fitNote: (p) => PLAT[p].engine.split(',').pop().trim() + ' engine' },
    { key: 'bbk', brand: 'halo', cat: 'brakes', sub: 'big-brake-kits', name: '6-Piston Front Big Brake Kit', base: 2199, spread: 900, weight: 60, plats: ['mustang', 'camaro', 'chall', 'charger', 'wrx'],
      features: ['Forged 6-piston calipers', '15" two-piece slotted rotors', 'Stainless brake lines included', 'Requires 19" or larger wheels'],
      specs: { 'Caliper': 'Forged aluminum, 6-piston', 'Rotor': '15 in. two-piece, slotted', 'Caliper Color': 'Red', 'Min Wheel Size': '19 in.', 'Warranty': '2 years' }, kw: 'big brake kit calipers rotors' },
    { key: 'pads', brand: 'halo', cat: 'brakes', sub: 'pads-rotors', name: 'Front Drilled & Slotted Rotors with Ceramic Pads', base: 229, spread: 140, weight: 38, plats: ALL,
      features: ['Drilled and slotted rotors run cooler', 'Low-dust ceramic pads', 'Zinc-plated to resist rust', 'Hardware included'],
      specs: { 'Rotor Type': 'Drilled and slotted', 'Pad Compound': 'Ceramic', 'Position': 'Front', 'Coating': 'Zinc plated', 'Warranty': '2 years' }, kw: 'brake pads rotors' },
    { key: 'wheel', brand: 'gridiron', cat: 'wheels', sub: 'wheels', name: '17x9 Beadlock-Style Wheel', base: 239, spread: 80, weight: 29, plats: TRUCKS.concat(JEEPS),
      features: ['Simulated beadlock ring with real bolts', 'Cast aluminum, flow-formed barrel', 'Matte black with red accent ring', 'Sold individually'],
      specs: { 'Size': '17 x 9 in.', 'Offset': '-12 mm', 'Material': 'Cast aluminum', 'Finish': 'Matte black / red ring', 'Load Rating': '2,500 lb', 'Warranty': '5 years structural' }, kw: 'wheels rims beadlock',
      dyn: (p) => ({ 'Bolt Pattern': PLAT[p].bolt }) },
    { key: 'spacer', brand: 'gridiron', cat: 'wheels', sub: 'wheel-spacers', name: '1.5" Hub-Centric Wheel Spacers (Pair)', base: 99, spread: 40, weight: 6, plats: TRUCKS.concat(JEEPS),
      features: ['Hub-centric for vibration-free fit', '6061-T6 billet aluminum', 'Grade 10.9 studs', 'Pair'],
      specs: { 'Thickness': '1.5 in.', 'Material': '6061-T6 aluminum', 'Hub-Centric': 'Yes', 'Quantity': '2', 'Warranty': 'Lifetime limited' }, kw: 'wheel spacers',
      dyn: (p) => ({ 'Bolt Pattern': PLAT[p].bolt }) },

    { key: 'at', brand: 'roadgrip', cat: 'tires', sub: 'all-terrain', name: 'Trail Pro A/T All-Terrain Tire', base: 209, spread: 90, weight: 52, plats: TRUCKS.concat(JEEPS),
      features: ['3-ply sidewall for puncture resistance', 'Severe-snow rated (3PMSF)', 'Stone ejectors in the shoulder grooves', 'Sold individually'],
      specs: { 'Tire Type': 'All-terrain', 'Load Range': 'E', 'Tread Depth': '15/32 in.', 'Mileage Warranty': '55,000 miles', 'Snow Rating': '3PMSF' }, kw: 'tires tire all terrain at',
      dyn: (p) => ({ 'Tire Size': PLAT[p].tire }), fitNote: (p) => 'Factory size ' + PLAT[p].tire },
    { key: 'mt', brand: 'roadgrip', cat: 'tires', sub: 'mud-terrain', name: 'Mud Brawler M/T Mud-Terrain Tire', base: 259, spread: 110, weight: 60, plats: TRUCKS.concat(JEEPS),
      features: ['Aggressive staggered shoulder lugs', 'Self-cleaning tread voids', '3-ply sidewall with lug armor', 'Sold individually'],
      specs: { 'Tire Type': 'Mud-terrain', 'Load Range': 'E', 'Tread Depth': '19/32 in.', 'Mileage Warranty': '40,000 miles' }, kw: 'tires tire mud terrain mt',
      dyn: (p) => ({ 'Tire Size': PLAT[p].tire }), fitNote: (p) => 'Factory size ' + PLAT[p].tire },
    { key: 'perf', brand: 'roadgrip', cat: 'tires', sub: 'performance-tires', name: 'Apex Sport UHP Summer Tire', base: 189, spread: 90, weight: 26, plats: MUSCLE.concat(SPORT),
      features: ['Ultra-high-performance summer compound', 'Stiff shoulder blocks for cornering grip', 'Not for use in snow or freezing temperatures', 'Sold individually'],
      specs: { 'Tire Type': 'Summer performance', 'Speed Rating': 'Y (186 mph)', 'Treadwear': '300', 'Mileage Warranty': 'None' }, kw: 'tires tire performance summer uhp',
      dyn: (p) => ({ 'Tire Size': PLAT[p].tire }), fitNote: (p) => 'Factory size ' + PLAT[p].tire },
    { key: 'as', brand: 'roadgrip', cat: 'tires', sub: 'all-season', name: 'CityMile All-Season Tire', base: 149, spread: 70, weight: 30, plats: ALL,
      features: ['Quiet, comfortable highway tread', 'Year-round traction including light snow', 'Long-wearing compound', 'Sold individually'],
      specs: { 'Tire Type': 'All-season', 'Speed Rating': 'H (130 mph)', 'Treadwear': '700', 'Mileage Warranty': '70,000 miles' }, kw: 'tires tire all season',
      dyn: (p) => ({ 'Tire Size': PLAT[p].tire }), fitNote: (p) => 'Factory size ' + PLAT[p].tire },
    // Universal parts (no vehicle-specific fitment)
    { key: 'bar30', brand: 'lumen', cat: 'lighting', sub: 'light-bars', name: '30" Dual-Row LED Light Bar', base: 189, spread: 1, weight: 9, universal: true,
      features: ['Combo spot/flood beam pattern', '13,500 raw lumens', 'IP68 waterproof', 'Wiring harness with switch included'],
      specs: { 'Length': '30 in.', 'Rows': 'Dual', 'Raw Lumens': '13,500', 'Beam Pattern': 'Spot / flood combo', 'Waterproof Rating': 'IP68', 'Warranty': '5 years' }, kw: 'light bar led offroad' },
    { key: 'bar50', brand: 'lumen', cat: 'lighting', sub: 'light-bars', name: '50" Curved LED Light Bar', base: 349, spread: 1, weight: 15, universal: true,
      features: ['Curved housing for a wider spread', '24,000 raw lumens', 'IP68 waterproof', 'Wiring harness with switch included'],
      specs: { 'Length': '50 in.', 'Rows': 'Dual, curved', 'Raw Lumens': '24,000', 'Beam Pattern': 'Spot / flood combo', 'Waterproof Rating': 'IP68', 'Warranty': '5 years' }, kw: 'light bar led roof curved' },
    { key: 'strap', brand: 'ridgeline', cat: 'exterior', sub: 'recovery', name: '3" x 30 ft Kinetic Recovery Rope', base: 129, spread: 1, weight: 8, universal: true,
      features: ['Stretches up to 30% for smooth recoveries', '30,000 lb minimum break strength', 'Abrasion-resistant sleeves on the eyes', 'Storage bag included'],
      specs: { 'Diameter': '3/4 in.', 'Length': '30 ft', 'Break Strength': '30,000 lb', 'Material': 'Double-braided nylon', 'Warranty': '1 year' }, kw: 'recovery strap rope tow kinetic' },
    { key: 'shackle', brand: 'ridgeline', cat: 'exterior', sub: 'recovery', name: 'Soft Shackles (2-Pack)', base: 39, spread: 1, weight: 1, universal: true,
      features: ['Lighter and safer than steel shackles', '52,000 lb break strength', 'UV-resistant synthetic', '2-pack'],
      specs: { 'Break Strength': '52,000 lb', 'Material': 'UHMWPE synthetic', 'Quantity': '2', 'Warranty': '1 year' }, kw: 'soft shackle recovery' }
  ];

  function hash(s) { let h = 2166136261; for (let i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); } return h >>> 0; }

  const brandById = Object.fromEntries(BRANDS.map((b) => [b.id, b]));
  const PRODUCTS = [];

  T.forEach((t) => {
    const list = t.universal ? [null] : t.plats;
    list.forEach((p) => {
      const seed = hash(t.key + ':' + (p || 'u'));
      const brand = brandById[t.brand];
      const pn = brand.prefix + '-' + String(10000 + (seed % 89999));
      const price = Math.round(t.base + (seed % t.spread)) - 0.01;
      const v = p ? PLAT[p] : null;
      const title = v ? `${brand.name} ${t.name} for ${v.from}-${v.to} ${v.make} ${v.model}` : `${brand.name} ${t.name}`;
      const notes = [];
      if (v && v.note) notes.push(v.note);
      if (t.fitNote) { const n = typeof t.fitNote === 'function' ? t.fitNote(p) : t.fitNote; if (n) notes.push(n); }
      const t14 = seed % 4 === 0 ? 0 : (seed >> 3) % 48;
      const key = (seed >> 7) % 5 === 0 ? 0 : (seed >> 9) % 31;
      PRODUCTS.push({
        id: pn.toLowerCase(),
        pn, brand: brand.id, brandName: brand.name, cat: t.cat, sub: t.sub, tpl: t.key,
        title, shortName: t.name, price, msrp: Math.round(price * 1.18) - 0.01,
        weight: t.weight,
        universal: !!t.universal,
        features: t.features,
        specs: Object.assign({ 'Part Number': pn, 'Brand': brand.name, 'Fitment Type': t.universal ? 'Universal' : 'Direct fit' }, t.specs, t.dyn ? t.dyn(p) : {}, { 'Shipping Weight': t.weight + ' lb' }),
        fitment: v ? [{ from: v.from, to: v.to, make: v.make, model: v.model, sub: v.sub, engine: v.engine, notes: notes.join('; ') }] : [],
        stock: { 'Turn14': t14, 'Keystone': key },
        popularity: seed % 1000,
        kw: [t.kw || '', t.name, brand.name, pn].join(' ').toLowerCase()
      });
    });
  });

  // Showcase items with real product photos (supplied by Dream Auto Builders) for the home page
  const SHOW = [
    { pn: 'DD5178', brand: 'diode', cat: 'lighting', sub: 'headlights', img: 'img/p-headlights.jpg', price: 1599.95, weight: 32,
      title: 'Diode Dynamics Elite LED Headlights for 2021-2023 Ford F-150 Platinum', shortName: 'Elite LED Headlights',
      features: ['Dual Bi-LED projectors for maximum output and intensity', 'Sequential amber turn signals', 'Plug-and-play wiring harness included', 'Sealed, DOT-compliant housings'],
      specs: { 'Light Source': 'Bi-LED projector', 'Housing': 'Black', 'Quantity': 'Pair', 'Compliance': 'DOT / SAE' },
      fitment: [{ from: 2021, to: 2023, make: 'Ford', model: 'F-150', sub: 'Platinum', engine: 'All', notes: 'Platinum trim with factory LED headlights' }] },
    { pn: '19296', brand: 'magnaflow', cat: 'exhaust', sub: 'axle-back', img: 'img/p-exhaust.jpg', price: 1991.47, weight: 38,
      title: 'MagnaFlow 2012-2017 Lexus LS460 Street Series Axle-Back Performance Exhaust System', shortName: 'Street Series Axle-Back Exhaust',
      features: ['Durable, corrosion-resistant stainless steel construction', 'CNC precision robotic welding', 'Smooth, deep tone with no interior drone', 'Bolt-on installation'],
      specs: { 'Material': 'Stainless steel', 'Sound Level': 'Mild', 'Tip Finish': 'Polished', 'Emissions': '50-state legal' },
      fitment: [{ from: 2012, to: 2017, make: 'Lexus', model: 'LS460', sub: 'Base, F Sport', engine: '4.6L V8', notes: '' }] },
    { pn: '250572', brand: 'mickey', cat: 'wheels', sub: 'wheels', img: 'img/p-wheel.jpg', price: 241.27, weight: 30, fitText: 'Fits by bolt pattern',
      title: 'Mickey Thompson 250572 Light Truck Wheel', shortName: 'Light Truck Wheel',
      features: ['All-new center cap gives easy access to locking hubs', 'Non-corrosive black plastic center cap', 'Matte black finish', 'Sold individually'],
      specs: { 'Finish': 'Matte black', 'Material': 'Cast aluminum', 'Center Cap': 'Included' },
      fitment: [], universal: true },
    { pn: 'DL1201U', brand: 'autometer', cat: 'performance', sub: 'gauges', img: 'img/p-dashcontrol.jpg', price: 328.81, weight: 2,
      title: 'AutoMeter DL1201U DashControl Display Controller for 2009-2014 Ford F-150', shortName: 'DashControl Display Controller',
      features: ['Take control of your factory instruments to unlock hidden performance info', 'Shows boost, air/fuel and temperatures on the stock display', 'Plug-in installation', 'Updatable over USB'],
      specs: { 'Display': 'Factory instrument cluster', 'Connection': 'OBD-II', 'Updates': 'USB' },
      fitment: [{ from: 2009, to: 2014, make: 'Ford', model: 'F-150', sub: 'XL, XLT, Lariat, FX4', engine: 'All', notes: '12th generation' }] }
  ];
  SHOW.forEach((x, i) => {
    const b = brandById[x.brand];
    PRODUCTS.unshift(Object.assign({
      id: (b.prefix + '-' + x.pn).toLowerCase(), brandName: b.name, tpl: 'show', msrp: Math.round(x.price * 1.12) - 0.01, universal: false, showcase: true,
      stock: { 'Turn14': 12 + i * 3, 'Keystone': 4 + i }, popularity: 2000 - i,
      kw: [b.name, x.title, x.pn, x.sub].join(' ').toLowerCase()
    }, x, { specs: Object.assign({ 'Part Number': x.pn, 'Brand': b.name }, x.specs, { 'Shipping Weight': x.weight + ' lb' }) }));
  });
  PRODUCTS.unshift(...PRODUCTS.splice(0, SHOW.length).reverse());

  // Real catalog imported from SEMA Data (see readylift.js)
  (window.DAB_RL || []).forEach((p) => {
    if (p.price == null) return;               // no price in the feed yet, keep off the storefront
    if (!p.fitPending && !p.fitment.length) p.fitText = 'See fitment notes';
    PRODUCTS.push(p);
  });

  // MagnaFlow (sample of the full catalog) imported from SEMA Data (see mf.js)
  (window.DAB_MF || []).forEach((p) => {
    if (p.price == null) return;
    const dup = PRODUCTS.findIndex((x) => x.id === p.id);
    if (dup >= 0) { p.showcase = PRODUCTS[dup].showcase; p.popularity = PRODUCTS[dup].popularity; PRODUCTS.splice(dup, 1, p); return; }
    PRODUCTS.push(p);
  });

  // Vehicle lists for the Year / Make / Model selectors
  // Sample platforms + real vehicles from SEMA fitment (ReadyLift), as year lists per make/model
  const VEH = Object.values(PLAT).map((p) => { const years = []; for (let y = p.from; y <= p.to; y++) years.push(y); return { make: p.make, model: p.model, years }; })
    .concat(window.DAB_RL_VEH || []).concat(window.DAB_MF_VEH || []);
  const allYears = VEH.flatMap((v) => v.years);
  const minY = Math.min(...allYears), maxY = Math.max(...allYears);
  const YEARS = []; for (let y = maxY; y >= minY; y--) if (allYears.includes(y)) YEARS.push(y);
  function makesFor(y) { return [...new Set(VEH.filter((v) => v.years.includes(y)).map((v) => v.make))].sort(); }
  function modelsFor(y, make) { return [...new Set(VEH.filter((v) => v.make === make && v.years.includes(y)).map((v) => v.model))].sort(); }
  const MODEL_NAMES = [...new Set(VEH.map((v) => v.make + '|' + v.model))].map((s) => { const [make, model] = s.split('|'); return { make, model }; })
    .sort((a, b) => b.model.length - a.model.length);   // longest names first so "F-250 Super Duty" wins over "F-250"

  return { BRANDS, CATS, PRODUCTS, YEARS, makesFor, modelsFor, MODEL_NAMES, synced: '2026-10-31T06:00:00' };
})();
