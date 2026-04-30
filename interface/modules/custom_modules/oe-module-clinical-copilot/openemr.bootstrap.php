<?php

/**
 * AgentForge Clinical Co-Pilot module bootstrap.
 *
 * Registers the PSR-4 namespace for the module and instantiates the
 * Bootstrap class which wires up the patient menu subscriber.
 *
 * @package   OpenEMR
 * @link      https://www.open-emr.org
 * @author    AgentForge Team
 * @copyright Copyright (c) 2026 AgentForge
 * @license   https://github.com/openemr/openemr/blob/master/LICENSE GNU General Public License 3
 */

declare(strict_types=1);

use OpenEMR\Core\ModulesClassLoader;
use OpenEMR\Modules\ClinicalCopilot\Bootstrap;

/**
 * @var ModulesClassLoader $classLoader Injected by the OpenEMR module loader.
 */
$classLoader->registerNamespaceIfNotExists(
    'OpenEMR\\Modules\\ClinicalCopilot\\',
    __DIR__ . DIRECTORY_SEPARATOR . 'src'
);

/**
 * @var \Symfony\Component\EventDispatcher\EventDispatcherInterface $eventDispatcher
 * @global $eventDispatcher @see ModulesApplication::loadCustomModule
 */

$bootstrap = new Bootstrap($eventDispatcher);
$bootstrap->subscribeToEvents();
