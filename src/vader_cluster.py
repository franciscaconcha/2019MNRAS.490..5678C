from amuse.lab import *
import numpy
from amuse.community.fractalcluster.interface import new_fractal_cluster_model
from amuse.ic.kingmodel import new_king_model
from scipy import interpolate
import multiprocessing
from decorators import timer
import os

# Workaround for now
global diverged_disks, disk_codes_indices
diverged_disks = {}
disk_codes_indices = {}


def column_density(grid,
                   rc,
                   mass,
                   lower_density=1E-12 | units.g / units.cm**2):
    """ Disk column density definition as in Eqs. 1, 2, and 3 of the paper.
        (Lynden-Bell & Pringle, 1974: Anderson et al. 2013)

    :param grid: disk grid
    :param rc: characteristic disk radius
    :param mass: disk mass
    :param lower_density: density limit for defining disk edge
    :return: disk column density in g / cm**2
    """
    r = grid.value_in(units.au) | units.au
    rd = rc  # Anderson et al. 2013
    Md = mass

    Sigma_0 = Md / (2 * numpy.pi * rc ** 2 * (1 - numpy.exp(-rd / rc)))
    Sigma = Sigma_0 * (rc / r) * numpy.exp(-r / rc) * (r <= rc) + lower_density
    return Sigma


def initialize_vader_code(disk_radius,
                          disk_mass,
                          stellar_mass,
                          alpha,
                          r_min=0.05 | units.au,
                          r_max=2000 | units.au,
                          n_cells=100,
                          linear=True):
    """ Initialize vader code for given parameters.

    :param disk_radius: disk radius. Must have units.au
    :param disk_mass: disk mass. Must have units.MSun
    :param stellar_mass: mass of the central star. Must have units.MSun
    :param alpha: turbulence parameter for viscosity, adimensional
    :param r_min: minimum radius of vader grid. Must have units.au
    :param r_max: maximum radius of vader grid. Must have units.au
    :param n_cells: number of cells for vader grid
    :param linear: linear interpolation
    :return: instance of vader code
    """
    disk = vader(redirection='none')
    disk.initialize_code()
    disk.initialize_keplerian_grid(
        n_cells,  # Number of cells
        linear,  # Linear?
        r_min,  # Grid Rmin
        r_max,  # Grid Rmax
        stellar_mass  # Mass of the central star
    )

    #disk.parameters.verbosity = 1

    sigma = column_density(disk.grid.r, disk_radius, disk_mass)
    disk.grid.column_density = sigma

    # The pressure follows the ideal gas law with a mean molecular weight of 2.33 hydrogen masses.
    # Since gamma ~ 1, this should not influence the simulation
    mH = 1.008 * constants.u
    T = 100. | units.K
    disk.grid.pressure = sigma * constants.kB * T / (2.33 * mH)

    disk.parameters.inner_pressure_boundary_type = 1
    disk.parameters.inner_pressure_boundary_torque = 0.0 | units.g * units.cm ** 2 / units.s ** 2
    disk.parameters.alpha = alpha
    disk.parameters.maximum_tolerated_change = 1E99
    global diverged_disks
    diverged_disks[disk] = False

    return disk


def evolve_parallel_disks(codes,
                          dt):
    """ This function will change depending on architecture.
        For running on Cartesius we made use of vader's internal parallelization
        through MPI, so not much to see here."""


    n_cpu = multiprocessing.cpu_count()
    processes = []
    threads = []
    #import time
    #print "Starting processes... n_cpu = {0}".format(n_cpu)
    #startt = time.time()
    #print("Start loop")
    #end = time.time()
    for i in range(len(codes)):
        #p = multiprocessing.Process(name=str(i), target=evolve_single_disk, args=(codes[i], dt, ))
        #processes.append(p)
        #p.start()

        #start = time.time()
        #print("code", i)
        #end = time.time()
        evolve_single_disk(codes[i], dt)
        #print(end - start)
    #endt = time.time()
    #print("End loop",len(codes))
    #print(endt - startt)
    #th = threading.Thread(target=evolve_single_disk, args=[codes[i], dt])
    #th.daemon = True
    #threads.append(th)
    #th.start()

    #for t in threads:
    #    t.join()

    #for p in processes:
    #    p.join()

    #print "All processes finished"


def evolve_single_disk(code,
                       dt):
    """ Evolve a single vader disk to time dt.

    :param code: vader code instance to evolve
    :param dt: time for evolution
    :return: vader code
    """
    #print "current process: {0}".format(multiprocessing.current_process().name)
    disk = code
    try:
        disk.evolve_model(dt)
    except:
        print "Disk did not converge"
        global diverged_disks
        diverged_disks[disk] = True


def get_disk_radius(disk,
                    density_limit=1E-10):
    """ Calculate the radius of a disk in a vader grid.

    :param disk: vader disk
    :param density_limit: density limit to designate disk border
    :return: disk radius in units.au
    """
    prev_r = disk.grid[0].r

    for i in range(len(disk.grid.r)):
        cell_density = disk.grid[i].column_density.value_in(units.g / units.cm ** 2)
        if cell_density < density_limit:
            return prev_r.value_in(units.au) | units.au
        prev_r = disk.grid[i].r

    return prev_r.value_in(units.au) | units.au


def get_disk_mass(disk,
                  radius):
    """ Calculate the mass of a vader disk inside a certain radius.

    :param disk: vader disk
    :param radius: disk radius to consider for mass calculation
    :return: disk mass in units.MJupiter
    """
    mass_cells = disk.grid.r[disk.grid.r <= radius]
    total_mass = 0

    for m, d, a in zip(mass_cells, disk.grid.column_density, disk.grid.area):
        total_mass += d.value_in(units.MJupiter / units.cm**2) * a.value_in(units.cm**2)

    return total_mass | units.MJupiter


def get_disk_density(disk):
    """ Calculate the mean density of the disk, not considering the outer, low density limit.

    :param disk: vader disk
    :return: mean disk density in g / cm**2
    """
    radius = get_disk_radius(disk)
    radius_index = numpy.where(disk.grid.r.value_in(units.au) == radius.value_in(units.au))
    density = disk.grid[:radius_index[0][0]].column_density.value_in(units.g / units.cm**2)
    return numpy.mean(density) | (units.g / units.cm**2)


def accretion_rate(mass):

    return numpy.power(10, (1.89 * numpy.log10(mass.value_in(units.MSun)) - 8.35)) | units.MSun / units.yr


def truncate_disk(disk,
                  new_radius,
                  density_limit=1E-11):
    """ Truncate a vader disk.

    :param disk: vader disk
    :param new_radius: new radius of disk
    :param density_limit: density limit for disk boundary definition
    :return: vader code with disk of radius = new_radius
    """

    disk.grid[disk.grid.r > new_radius].column_density = density_limit | units.g / units.cm**2
    return disk


def evaporate(disk,
              mass_to_remove):
    """ Return new size disk after photoevaporation.
        Goes through the disk cells outside-in, removing mass until the needed amount is reached.

    :param disk: vader disk
    :param mass_to_remove: mass lost due to photoevaporation in MSun
    :return: vader code with updated disk
    """

    radius = get_disk_radius(disk).value_in(units.au)

    init_cell = numpy.where(disk.grid.r.value_in(units.au) == radius)[0][0]

    swiped_mass = 0.0 | mass_to_remove.unit

    for i in range(init_cell)[::-1]:
        r = disk.grid[i].r
        d = disk.grid[i].column_density
        a = disk.grid[i].area

        cell_mass_msun = d.value_in(mass_to_remove.unit / (units.au ** 2)) * a.value_in(units.au ** 2) | mass_to_remove.unit
        swiped_mass += cell_mass_msun

        if swiped_mass < mass_to_remove:
            continue
        else:
            if i == 0:
                return None
            else:
                return truncate_disk(disk, r)


def distance(star1,
             star2):
    """ Return distance between star1 and star2

    :param star1: AMUSE particle
    :param star2: AMUSE particle
    :return: distance in units.parsec
    """
    return numpy.sqrt((star2.x - star1.x)**2 + (star2.y - star1.y)**2 + (star2.z - star1.z)**2)


def radiation_at_distance(rad, d):
    """ Return radiation rad at distance d

    :param rad: total radiation from star in erg/s
    :param d: distance in cm
    :return: radiation of star at distance d, in erg * s^-1 * cm^-2
    """
    return rad / (4 * numpy.pi * d**2) | (units.erg / (units.s * units.cm**2))


def find_indices(column,
                 val):
    """
    Return indices of column values in between which val is located.
    Return i,j such that column[i] < val < column[j]

    :param column: column where val is to be located
    :param val: number to be located in column
    :return: i, j indices
    """

    # The largest element of column less than val
    try:
        value_below = column[column < val].max()
    except ValueError:
        # If there are no values less than val in column, return smallest element of column
        value_below = column.min()
    # Find index
    index_i = numpy.where(column == value_below)[0][0]

    # The smallest element of column greater than val
    try:
        value_above = column[column > val].min()
    except ValueError:
        # If there are no values larger than val in column, return largest element of column
        value_above = column.max()
    # Find index
    index_j = numpy.where(column == value_above)[0][0]

    return int(index_i), int(index_j)


def luminosity_fit(mass):
    """
    Return stellar luminosity (in LSun) for corresponding mass, as calculated with Martijn's fit

    :param mass: stellar mass in MSun
    :return: stellar luminosity in LSun
    """
    if 0.12 < mass < 0.24:
        return (1.70294E16 * numpy.power(mass, 42.557)) | units.LSun
    elif 0.24 < mass < 0.56:
        return (9.11137E-9 * numpy.power(mass, 3.8845)) | units.LSun
    elif 0.56 < mass < 0.70:
        return (1.10021E-6 * numpy.power(mass, 12.237)) | units.LSun
    elif 0.70 < mass < 0.91:
        return (2.38690E-4 * numpy.power(mass, 27.199)) | units.LSun
    elif 0.91 < mass < 1.37:
        return (1.02477E-4 * numpy.power(mass, 18.465)) | units.LSun
    elif 1.37 < mass < 2.07:
        return (9.66362E-4 * numpy.power(mass, 11.410)) | units.LSun
    elif 2.07 < mass < 3.72:
        return (6.49335E-2 * numpy.power(mass, 5.6147)) | units.LSun
    elif 3.72 < mass < 10.0:
        return (6.99075E-1 * numpy.power(mass, 3.8058)) | units.LSun
    elif 10.0 < mass < 20.2:
        return (9.73664E0 * numpy.power(mass, 2.6620)) | units.LSun
    elif 20.2 < mass:
        return (1.31175E2 * numpy.power(mass, 1.7974)) | units.LSun
    else:
        return 0 | units.LSun


def periastron_distance(stars):
    """ Return the periastron distance of two encountering stars.

    :param stars: pair of encountering stars
    :return: periastron distance of the encounter
    """
    # Standard gravitational parameter
    mu = constants.G * stars.mass.sum()

    # Position vector from one star to the other
    r = stars[0].position - stars[1].position

    # Relative velocity between the stars
    v = stars[0].velocity - stars[1].velocity

    # Energy
    E = (v.length()) ** 2 / 2 - mu / r.length()

    # Semi-major axis
    a = -mu / 2 / E

    # Semi-latus rectum
    p = (numpy.cross(r.value_in(units.au),
                  v.value_in(units.m / units.s)) | units.au * units.m / units.s).length() ** 2 / mu

    # Eccentricity
    e = numpy.sqrt(1 - p / a)

    # Periastron distance
    return p / (1 + e)


def resolve_encounter(stars,
                      disk_codes,
                      time,
                      verbose=False):
    """ Resolve dynamical encounter between two stars.

    :param stars: pair of encountering stars, array of 2 AMUSE particles
    :param disk_codes: vader codes of the disks in the encounter
    :param time: time at which encounter occurs
    :param verbose: verbose option for debugging
    :return: updated vader disk codes
    """
    # For debugging
    if verbose:
        print(time.value_in(units.yr), stars.mass.value_in(units.MSun))

    closest_approach = periastron_distance(stars)
    # Update collisional radius so that we don't detect this encounter in the next time step
    stars.collisional_radius = 0.49 * closest_approach

    new_codes = []
    truncated = False

    # Check each star
    for i in range(2):
        if disk_codes[i] is None:  # Bright star, no disk code
            new_codes.append(None)
        else:
            truncation_radius = (closest_approach.value_in(units.au) / 3) *\
                                  (stars[i].stellar_mass.value_in(units.MSun)
                                  / stars[1 - i].stellar_mass.value_in(units.MSun)) ** 0.32 | units.au

            R_disk = stars[i].disk_radius
            print "R_disk = {0}, truncation radius={1}".format(R_disk, truncation_radius)

            if truncation_radius < R_disk:
                truncated = True
                print "truncating encounter"
                stars[i].encounters += 1

                if truncation_radius <= 0.5 | units.au:
                    # Disk is dispersed. Have to handle this here or vader crashes for such small radii.
                    stars[i].dispersed = True
                    stars[i].disk_radius = truncation_radius
                    stars[i].disk_mass = 0.0 | units.MJupiter
                    stars[i].dispersal_time = time
                    stars[i].truncation_mass_loss = stars[i].disk_mass
                    stars[i].cumulative_truncation_mass_loss += stars[i].disk_mass
                    new_codes.append(disk_codes[i])

                else:
                    old_mass = get_disk_mass(disk_codes[i], R_disk)

                    new_disk = truncate_disk(disk_codes[i], truncation_radius)
                    new_codes.append(new_disk)

                    new_mass = get_disk_mass(new_disk, truncation_radius)

                    stars[i].truncation_mass_loss = old_mass - new_mass
                    stars[i].cumulative_truncation_mass_loss += old_mass - new_mass
                    stars[i].disk_mass = new_mass
                    stars[i].mass = stars[i].stellar_mass + stars[i].disk_mass

            else:
                new_codes.append(disk_codes[i])

            # Truncating the "no photoevaporation" disk, if needed
            if truncation_radius < stars[i].disk_size_np:
                stars[i].disk_size_np = truncation_radius
                stars[i].disk_mass_np *= 1.6

    return truncated, new_codes


@timer
def main(N, Rvir, Qvir, dist, alpha, ncells, t_ini, t_end, save_interval, run_number, save_path, dt):

    try:
        float(t_end)
        t_end = t_end | units.Myr
    except TypeError:
        pass

    t = 0.0 | t_end.unit

    path = "{0}/{1}/".format(save_path, run_number)
    try:
        os.makedirs(path)
        print "Results path created"
    except OSError, e:
        if e.errno != 17:
            raise
        pass

    max_stellar_mass = 100 | units.MSun
    stellar_masses = new_kroupa_mass_distribution(N, max_stellar_mass)#, random=False)
    converter = nbody_system.nbody_to_si(stellar_masses.sum(), Rvir)

    # Spatial distribution, default is Plummer sphere
    if dist == "king":
        stars = new_king_model(N, W0=3, convert_nbody=converter)
    elif dist == "fractal":
        stars = new_fractal_cluster_model(N=N, fractal_dimension=1.6, convert_nbody=converter)
    else:
        stars = new_plummer_model(N, converter)

    stars.scale_to_standard(converter, virial_ratio=Qvir)

    stars.stellar_mass = stellar_masses
    stars.encounters = 0  # Counter for dynamical encounters

    # Bright stars: no disks; emit FUV radiation
    bright_stars = stars[stars.stellar_mass.value_in(units.MSun) > 1.9]

    if len(bright_stars) == 0:  # For small tests sometimes we don't get any stars > 1.9MSun, so we add one
        big_star = numpy.random.uniform(low=2, high=50)
        stars[0].stellar_mass = big_star | units.MSun
        bright_stars = stars[stars.stellar_mass.value_in(units.MSun) > 1.9]
        print("Warning: No star with mass > 1.9 MSun generated by the IMF."
              "\nOne star of {0} MSun added to the simulation.".format(big_star))
    bright_stars.bright = True

    # Small stars: with disks; radiation from them not considered
    small_stars = stars[stars.stellar_mass.value_in(units.MSun) <= 1.9]
    small_stars.bright = False

    small_stars.disk_radius = 100 * (small_stars.stellar_mass.value_in(units.MSun) ** 0.5) | units.au
    bright_stars.disk_radius = 0 | units.au

    bright_stars.disk_mass = 0 | units.MSun
    small_stars.disk_mass = 0.1 * small_stars.stellar_mass

    stars.mass = stars.stellar_mass + stars.disk_mass

    # Initially all stars have the same collisional radius
    stars.collisional_radius = 0.02 | units.parsec

    # Saving G0 on small stars
    stars.g0 = 0.0

    disk_codes = []
    global disk_codes_indices, diverged_disks
    disk_codes_indices = {}  # Using this to keep track of codes later on, for the encounters

    # Create individual instances of vader codes for each disk
    for s in stars:
        if s in small_stars:
            s.code = True
            s_code = initialize_vader_code(s.disk_radius,
                                           s.disk_mass,
                                           s.stellar_mass,
                                           alpha,
                                           n_cells=ncells,
                                           linear=False)

            s_code.parameters.inner_pressure_boundary_mass_flux = accretion_rate(s.stellar_mass)

            disk_codes.append(s_code)
            disk_codes_indices[s.key] = len(disk_codes) - 1
            diverged_disks[s_code] = False

            # Saving these values to keep track of dispersed disks later on
            s.dispersed_disk_mass = 0.01 * s.disk_mass  # Disk is dispersed if it has lost 99% of its initial mass
            s.dispersion_threshold = 1E-5 | units.g / units.cm**2  # Density threshold for dispersed disks, Ingleby+ 2009
            s.dispersed = False
            s.checked = False  # I need this to keep track of dispersed disk checks
            s.dispersal_time = t
            s.photoevap_mass_loss = 0 | units.MJupiter
            s.cumulative_photoevap_mass_loss = 0 | units.MJupiter
            s.truncation_mass_loss = 0 | units.MJupiter
            s.cumulative_truncation_mass_loss = 0 | units.MJupiter
            s.EUV = False  # For photoevaporation regime
            s.nearby_supernovae = False

            # Initial values of disks
            s.initial_disk_size = get_disk_radius(s_code)
            s.initial_disk_mass = get_disk_mass(s_code, s.initial_disk_size)

            # Value to keep track of disk sizes and masses as not influenced by photoevaporation
            s.disk_size_np = s.initial_disk_size
            s.disk_mass_np = s.initial_disk_mass

        else:  # Bright stars don't have an associated disk code
            s.code = False

    # Start gravity code, add all stars
    gravity = ph4(converter)
    gravity.parameters.timestep_parameter = 0.01
    gravity.parameters.epsilon_squared = (100 | units.au) ** 2
    gravity.particles.add_particles(stars)

    # Enable stopping condition for dynamical encounters
    dynamical_encounter = gravity.stopping_conditions.collision_detection
    dynamical_encounter.enable()

    # Start stellar evolution code, add only massive stars
    stellar = SeBa()
    stellar.parameters.metallicity = 0.02
    stellar.particles.add_particles(bright_stars)
    # Enable stopping on supernova explosion
    detect_supernova = stellar.stopping_conditions.supernova_detection
    detect_supernova.enable()

    # Communication channels
    channel_from_stellar_to_framework = stellar.particles.new_channel_to(stars)
    channel_from_stellar_to_gravity = stellar.particles.new_channel_to(gravity.particles)
    channel_from_gravity_to_framework = gravity.particles.new_channel_to(stars)
    channel_from_framework_to_gravity = stars.new_channel_to(gravity.particles,
                                                             attributes=['collisional_radius'],
                                                             target_names=['radius'])
    channel_from_framework_to_stellar = stars.new_channel_to(stellar.particles)

    channel_from_framework_to_gravity.copy()

    ######## FRIED grid ########
    # Read FRIED grid
    grid = numpy.loadtxt('../photoevap/data/friedgrid.dat', skiprows=2)

    # Getting only the useful parameters from the grid (not including Mdot)
    FRIED_grid = grid[:, [0, 1, 2, 4]]
    grid_log10Mdot = grid[:, 5]

    grid_stellar_mass = FRIED_grid[:, 0]
    grid_FUV = FRIED_grid[:, 1]
    grid_disk_mass = FRIED_grid[:, 2]
    grid_disk_radius = FRIED_grid[:, 3]

    E_ini = gravity.kinetic_energy + gravity.potential_energy

    # For keeping track of energy
    E_handle = file('{0}/{1}/energy.txt'.format(save_path, run_number), 'a')
    Q_handle = file('{0}/{1}/virial.txt'.format(save_path, run_number), 'a')
    E_list = []
    Q_list = []

    write_set_to_file(stars,
                      '{0}/{1}/N{2}_t{3}.hdf5'.format(save_path,
                                                      run_number,
                                                      N,
                                                      t.value_in(units.Myr)),
                      'hdf5')

    channel_from_stellar_to_framework.copy()
    channel_from_stellar_to_gravity.copy()
    channel_from_framework_to_stellar.copy()

    active_disks = len(small_stars)   # Counter for active disks

    # Evolve!
    while t < t_end:
        print "t=", t
        dt = min(dt, t_end - t)

        stellar.evolve_model(t + dt/2)
        channel_from_stellar_to_gravity.copy()
        channel_from_stellar_to_framework.copy()

        E_kin = gravity.kinetic_energy
        E_pot = gravity.potential_energy

        E_list.append([(E_kin + E_pot) / E_ini - 1])
        Q_list.append([-1.0 * E_kin / E_pot])

        gravity.evolve_model(t + dt)
        channel_from_gravity_to_framework.copy()

        if dynamical_encounter.is_set():  # Dynamical encounter detected
            encountering_stars = Particles(particles=[dynamical_encounter.particles(0)[0],
                                                      dynamical_encounter.particles(1)[0]])

            s0 = encountering_stars.get_intersecting_subset_in(stars)[0]
            s1 = encountering_stars.get_intersecting_subset_in(stars)[1]

            # This is to manage encounters involving bright stars (which have no associated vader code)
            try:
                code_index = [disk_codes_indices[encountering_stars[0].key],
                              disk_codes_indices[encountering_stars[1].key]]
                star_codes = [disk_codes[code_index[0]], disk_codes[code_index[1]]]
                print "small - small"
                print "key1: {0}, key2: {1}".format(encountering_stars[0].key, encountering_stars[1].key)
            except KeyError:
                if s0 in bright_stars and s1 in small_stars:
                    print "bright - small w/ disk"
                    if not s1.dispersed:  # Making sure that the small star still has a disk
                        code_index = [None, disk_codes_indices[s1.key]]
                        star_codes = [None, disk_codes[code_index[1]]]
                        print "key1: {0}, key2: {1}".format(s0.key, s1.key)
                    else:  # Small star's disk has been dispersed already
                        star_codes = [None, None]
                elif s1 in bright_stars and s0 in small_stars:
                    print "small w/ disk - bright"
                    if not s0.dispersed:
                        code_index = [disk_codes_indices[s0.key], None]
                        star_codes = [disk_codes[code_index[0]], None]
                        print "key1: {0}, key2: {1}".format(s0.key, s1.key)
                    else:
                        star_codes = [None, None]
                else:
                    print "bright - bright"
                    star_codes = [None, None]
                    print "key1: {0}, key2: {1}".format(s0.key, s1.key)

            truncated, new_codes = resolve_encounter(encountering_stars.get_intersecting_subset_in(stars),
                                                     star_codes,
                                                     gravity.model_time + t_ini)

            if truncated:  # At least one disk has been truncated in the encounter
                if new_codes[0] is not None and new_codes[1] is not None:
                    # small-small
                    disk_codes[code_index[0]] = new_codes[0]
                    disk_codes[code_index[1]] = new_codes[1]

                    # Updating radii
                    s0.disk_radius = get_disk_radius(disk_codes[code_index[0]])
                    s1.disk_radius = get_disk_radius(disk_codes[code_index[1]])

                elif new_codes[0] is None and new_codes[1] is not None:
                    # bright-small
                    disk_codes[code_index[1]] = new_codes[1]
                    s1 = encountering_stars.get_intersecting_subset_in(stars)[1]
                    s1.disk_radius = get_disk_radius(disk_codes[code_index[1]])

                elif new_codes[0] is not None and new_codes[1] is None:
                    # small-bright
                    disk_codes[code_index[0]] = new_codes[0]
                    s0 = encountering_stars.get_intersecting_subset_in(stars)[0]
                    s0.disk_radius = get_disk_radius(disk_codes[code_index[0]])

        # Copy stars' new collisional radii (updated in resolve_encounter) to gravity
        channel_from_framework_to_gravity.copy()

        # Evolve stellar evolution for remaining half time step
        stellar.evolve_model(dt/2)
        channel_from_stellar_to_gravity.copy()
        channel_from_stellar_to_framework.copy()

        # Detect supernova explosion after evolving stellar evolution
        # Delete star that went through supernova explosion
        # Delete all disks within 0.3 pc of the supernova explosion
        if detect_supernova.is_set():
            print "SUPERNOVA EXPLOSION!!!"
            channel_from_stellar_to_framework.copy()
            channel_from_stellar_to_gravity.copy()
            channel_from_gravity_to_framework.copy()
            particles_in_supernova = Particles(particles=detect_supernova.particles(0))
            supernova_star = particles_in_supernova.get_intersecting_subset_in(stars)

            # Parameters from Portegies Zwart 2018
            a = 66.018192
            b = 0.62602783
            c = -0.68226438
            induced_inclination = 0.0  # For now

            for n in small_stars:
                explosion_distance = distance(supernova_star, n)
                r_disk = a * explosion_distance.value_in(units.parsec) ** b * abs(numpy.cos(induced_inclination)) ** c
                if 0. < r_disk < n.disk_radius:
                    new_code = truncate_disk(disk_codes[disk_codes_indices[n.key]], r_disk)
                    disk_codes[disk_codes_indices[n.key]] = new_code
                    n.disk_radius = get_disk_radius(new_code)
                    n.disk_mass = get_disk_mass(new_code, n.disk_radius)
                    n.nearby_supernovae = True
                elif r_disk == 0.:
                    n.disk_radius = 0. | units.au
                    n.disk_mass = 0. | units.MSun
                    n.dispersed = True
                    n.nearby_supernovae = True
                    n.checked = True
                    n.dispersal_time = t
                    to_del = disk_codes_indices[n.key]
                    disk_codes[to_del].stop()
                    del disk_codes[to_del]  # Delete dispersed disk from code list
                    for i in disk_codes_indices:
                        if disk_codes_indices[i] > to_del:
                            disk_codes_indices[i] -= 1
                    del disk_codes_indices[n.key]
                    active_disks -= 1

            channel_from_framework_to_gravity.copy()
            del stars[stars.key == supernova_star.key]
            del bright_stars[bright_stars.key == supernova_star.key]

        # Viscous evolution
        evolve_parallel_disks(disk_codes, t + dt)

        # Check disks
        for s, c in zip(small_stars, disk_codes):
            if s.dispersed and not s.checked:  # Disk "dispersed" in truncation and star hasn't been checked yet
                s.disk_radius = 0. | units.au
                s.disk_mass = 0. | units.MSun
                s.checked = True
                s.code = False
                s.dispersal_time = t
                to_del = disk_codes_indices[s.key]
                disk_codes[to_del].stop()
                del disk_codes[to_del]  # Delete dispersed disk from code list
                for i in disk_codes_indices:
                    if disk_codes_indices[i] > to_del:
                        disk_codes_indices[i] -= 1
                del disk_codes_indices[s.key]
                active_disks -= 1
                print "Star's {0} disk dispersed in truncation, deleted code".format(s.key)
                continue

            if s.code and not s.checked:  # Star not checked yet
                # Check for diverged disks
                if diverged_disks[c]:  # Disk diverged
                    s.dispersed = True
                    s.code = False
                    s.checked = True
                    s.dispersal_time = t
                    c.stop()
                    to_del = disk_codes_indices[s.key]
                    disk_codes[to_del].stop()
                    del disk_codes[to_del]  # Delete dispersed disk from code list
                    for i in disk_codes_indices:
                        if disk_codes_indices[i] > to_del:
                            disk_codes_indices[i] -= 1
                    del disk_codes_indices[s.key]
                    active_disks -= 1
                    print "Star's {0} disk diverged, deleted code".format(s.key)
                    continue

                # Check for dispersed disks
                disk_density = get_disk_density(c)

                # Check for dispersed disks
                if s.disk_radius.value_in(units.au) < 0.5 or disk_density <= s.dispersion_threshold:
                    # Not checking for mass thresholds here, I do that after the photoevaporation step
                    s.disk_radius = 0. | units.au
                    s.disk_mass = 0. | units.MSun
                    s.dispersed = True
                    s.checked = True
                    s.code = False
                    s.dispersal_time = t
                    to_del = disk_codes_indices[s.key]
                    disk_codes[to_del].stop()
                    del disk_codes[to_del]  # Delete dispersed disk from code list
                    for i in disk_codes_indices:
                        if disk_codes_indices[i] > to_del:
                            disk_codes_indices[i] -= 1
                    del disk_codes_indices[s.key]
                    active_disks -= 1
                    print "Star's {0} disk dispersed because of density threshold, deleted code".format(s.key)
                    continue

            # Add accreted mass from disk to host star
            s.stellar_mass += c.inner_boundary_mass_out.value_in(units.MSun) | units.MSun

            # Update stars disk radius and mass
            s.disk_radius = get_disk_radius(c)
            s.disk_mass = get_disk_mass(c, s.disk_radius)

            s.mass = s.stellar_mass + s.disk_mass
        # End disk check/update


        ########### Photoevaporation ############

        # Calculate the total FUV contribution of the bright stars over each small star
        total_radiation = {}
        for ss in small_stars:
            total_radiation[ss.key] = 0.

        for s in bright_stars:  # For each massive/bright star
            # Calculate FUV luminosity of the bright star, in LSun
            lum = luminosity_fit(s.stellar_mass.value_in(units.MSun))

            for ss in small_stars[small_stars.dispersed == False]:
                # Calculate distance to bright star
                dist = distance(s, ss)

                # EUV regime -- Use Johnstone, Hollenbach, & Bally 1998
                dmin = 5. * 1E17 * 0.25 * numpy.sqrt(ss.disk_radius.value_in(units.cm) / 1E14) | units.cm

                if dist < dmin:
                    ss.EUV = True

                else:
                    # Other bright stars can still contribute FUV radiation
                    radiation_ss = radiation_at_distance(lum.value_in(units.erg / units.s),
                                                         dist.value_in(units.cm)
                                                         )

                    radiation_ss_G0 = radiation_ss.value_in(units.erg/(units.s * units.cm**2)) / 1.6E-3
                    total_radiation[ss.key] += radiation_ss_G0

        # Apply photoevaporation on small stars
        for ss in small_stars[small_stars.dispersed == False]:
            ss.g0 = total_radiation[ss.key]

            # EUV regime -- Use Johnstone, Hollenbach, & Bally 1998
            if ss.EUV:
                # Photoevaporative mass loss in MSun/yr. Eq 20 from Johnstone, Hollenbach, & Bally 1998
                # From the paper: e ~ 3, x ~ 1.5
                photoevap_Mdot = 2. * 1E-9 * 3 * 4.12 * (ss.disk_radius.value_in(units.cm) / 1E14)

                # Calculate total mass lost due to EUV photoevaporation during dt, in MSun
                total_photoevap_mass_loss_euv = float(photoevap_Mdot * dt.value_in(units.yr)) | units.MSun

                # Back to False for next time
                ss.EUV = False
            else:
                total_photoevap_mass_loss_euv = 0.0 | units.MSun

            # FUV regime -- Use FRIED grid

            # For the small star, I want to interpolate the photoevaporation mass loss
            # xi will be the point used for the interpolation. Adding star values...
            xi = numpy.ndarray(shape=(1, 4), dtype=float)
            xi[0][0] = ss.stellar_mass.value_in(units.MSun)
            xi[0][1] = total_radiation[ss.key]
            xi[0][3] = get_disk_radius(disk_codes[disk_codes_indices[ss.key]]).value_in(units.au)
            xi[0][2] = get_disk_mass(disk_codes[disk_codes_indices[ss.key]], xi[0][3] | units.au).value_in(units.MJupiter)

            # Building the subgrid (of FRIED grid) over which I will perform the interpolation
            subgrid = numpy.ndarray(shape=(8, 4), dtype=float)

            # Finding indices between which ss.mass is located in the grid
            stellar_mass_i, stellar_mass_j = find_indices(grid_stellar_mass, ss.stellar_mass.value_in(units.MSun))
            subgrid[0] = FRIED_grid[stellar_mass_i]
            subgrid[1] = FRIED_grid[stellar_mass_j]

            # Finding indices between which the radiation over the small star is located in the grid
            FUV_i, FUV_j = find_indices(grid_FUV, total_radiation[ss.key])
            subgrid[2] = FRIED_grid[FUV_i]
            subgrid[3] = FRIED_grid[FUV_j]

            # Finding indices between which ss.disk_mass is located in the grid
            disk_mass_i, disk_mass_j = find_indices(grid_disk_mass, ss.disk_mass.value_in(units.MJupiter))
            subgrid[4] = FRIED_grid[disk_mass_i]
            subgrid[5] = FRIED_grid[disk_mass_j]

            # Finding indices between which ss.disk_radius is located in the grid
            disk_radius_i, disk_radius_j = find_indices(grid_disk_radius, ss.disk_radius.value_in(units.au))
            subgrid[6] = FRIED_grid[disk_radius_i]
            subgrid[7] = FRIED_grid[disk_radius_j]

            # Adding known values of Mdot, in the indices found above, to perform interpolation
            Mdot_values = numpy.ndarray(shape=(8, ), dtype=float)
            indices_list = [stellar_mass_i, stellar_mass_j,
                            FUV_i, FUV_j,
                            disk_mass_i, disk_mass_j,
                            disk_radius_i, disk_radius_j]
            for x in indices_list:
                Mdot_values[indices_list.index(x)] = grid_log10Mdot[x]

            # Interpolate!
            # Photoevaporative mass loss in log10(MSun/yr)
            photoevap_Mdot = interpolate.griddata(subgrid, Mdot_values, xi, method="nearest")

            # Calculate total mass lost due to photoevaporation during dt, in MSun
            total_photoevap_mass_loss_fuv = float(numpy.power(10, photoevap_Mdot) * dt.value_in(units.yr)) | units.MSun

            total_photoevap_mass_loss = total_photoevap_mass_loss_euv + total_photoevap_mass_loss_fuv
            ss.photoevap_mass_loss = total_photoevap_mass_loss
            ss.cumulative_photoevap_mass_loss += total_photoevap_mass_loss

            if ss.cumulative_photoevap_mass_loss >= ss.initial_disk_mass or ss.disk_mass < 0.03 | units.MEarth:  # Ansdell+2016
                # Disk is gone by photoevaporation
                ss.disk_radius = 0. | units.au
                ss.disk_mass = 0. | units.MSun
                ss.dispersed = True
                ss.checked = True
                ss.code = False
                ss.dispersal_time = t
                to_del = disk_codes_indices[ss.key]
                disk_codes[to_del].stop()
                del disk_codes[to_del]  # Delete dispersed disk from code list
                for i in disk_codes_indices:
                    if disk_codes_indices[i] > to_del:
                        disk_codes_indices[i] -= 1
                del disk_codes_indices[ss.key]
                active_disks -= 1
                print "Star's {0} disk dispersed by photoevaporation, deleted code".format(ss.key)
                continue

            # Evaporate the calculated mass loss from the disk
            evaporated_disk = evaporate(disk_codes[disk_codes_indices[ss.key]],
                                        total_photoevap_mass_loss)

            # If evaporate returns None, the disk is gone
            if evaporated_disk is not None:
                disk_codes[disk_codes_indices[ss.key]] = evaporated_disk
            else:
                ss.disk_radius = 0. | units.au
                ss.disk_mass = 0. | units.MSun
                ss.dispersed = True
                ss.checked = True
                ss.code = False
                ss.dispersal_time = t
                to_del = disk_codes_indices[ss.key]
                disk_codes[to_del].stop()
                del disk_codes[to_del]  # Delete dispersed disk from code list
                for i in disk_codes_indices:
                    if disk_codes_indices[i] > to_del:
                        disk_codes_indices[i] -= 1
                del disk_codes_indices[ss.key]
                active_disks -= 1
                print "Star's {0} disk dispersed by photoevaporation, deleted code".format(ss.key)
                continue

            ss.disk_radius = get_disk_radius(disk_codes[disk_codes_indices[ss.key]])
            ss.disk_mass = get_disk_mass(disk_codes[disk_codes_indices[ss.key]], ss.disk_radius)

        ########### End Photoevaporation  ############

        channel_from_framework_to_gravity.copy()
        t += dt

        if active_disks <= 0:
            write_set_to_file(stars,
                              '{0}/{1}/N{2}_t{3}.hdf5'.format(save_path,
                                                              run_number,
                                                              N,
                                                              t.value_in(units.Myr)),
                              'hdf5')
            print "NO DISKS LEFT AT t = {0} Myr".format(t.value_in(units.Myr))
            print "saving! at t = {0} Myr".format(t.value_in(units.Myr))
            break

        if (numpy.around(t.value_in(units.yr)) % save_interval.value_in(units.yr)) == 0.:
            print "saving! at t = {0} Myr".format(t.value_in(units.Myr))
            write_set_to_file(stars,
                              '{0}/{1}/N{2}_t{3}.hdf5'.format(save_path,
                                                          run_number,
                                                          N,
                                                          t.value_in(units.Myr)),
                              'hdf5')

        numpy.savetxt(E_handle, E_list)
        numpy.savetxt(Q_handle, Q_list)

        E_list = []
        Q_list = []

    if active_disks > 0:
        print "SIMULATION ENDED AT t = {0} Myr".format(t_end.value_in(units.Myr))

    for d in disk_codes:
        print get_disk_radius(d)
        d.stop()

    gravity.stop()
    stellar.stop()


def new_option_parser():
    from amuse.units.optparse import OptionParser
    result = OptionParser()

    # Simulation parameters
    result.add_option("-n", dest="run_number", type="int", default=0,
                      help="run number [%default]")
    result.add_option("-s", dest="save_path", type="string", default='.',
                      help="path to save the results [%default]")
    result.add_option("-i", dest="save_interval", type="int", default=50000 | units.yr,
                      help="time interval of saving a snapshot of the cluster [%default]")

    # Cluster parameters
    result.add_option("-N", dest="N", type="int", default=100,
                      help="number of stars [%default]")
    result.add_option("-R", dest="Rvir", type="float",
                      unit=units.parsec, default=0.25,
                      help="cluster virial radius [%default]")
    result.add_option("-Q", dest="Qvir", type="float", default=0.5,
                      help="virial ratio [%default]")
    result.add_option("-p", dest="dist", type="string", default="plummer",
                      help="spatial distribution [%default]")

    # Disk parameters
    result.add_option("-a", dest="alpha", type="float", default=5E-3,
                      help="turbulence parameter [%default]")
    result.add_option("-c", dest="ncells", type="int", default=100,
                      help="Number of cells to be used in vader disk [%default]")

    # Time parameters
    result.add_option("-I", dest="t_ini", type="int", default=0 | units.yr,
                      help="initial time [%default]")
    result.add_option("-t", dest="dt", type="int", default=1000 | units.yr,
                      help="dt for simulation [%default]")
    result.add_option("-e", dest="t_end", type="float", default=2 | units.Myr,
                      help="end time of the simulation [%default]")

    return result


if __name__ == '__main__':
    o, arguments = new_option_parser().parse_args()
    main(**o.__dict__)
