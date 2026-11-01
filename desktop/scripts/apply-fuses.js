const { flipFuses, FuseVersion, FuseV1Options } = require('@electron/fuses');
const path = require('path');

/**
 * electron-builder afterPack hook:
 * Applies Electron production fuses to the packaged binary before NSIS installer creation.
 */
module.exports = async function applyFuses(context) {
  const { electronPlatformName, appOutDir, packager } = context;
  if (electronPlatformName !== 'win32') {
    return;
  }

  const exeName = `${packager.appInfo.productFilename}.exe`;
  const exePath = path.join(appOutDir, exeName);

  console.log(`\n[FUSES] Applying Electron production security fuses to ${exePath}...`);

  await flipFuses(exePath, {
    version: FuseVersion.V1,
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
    [FuseV1Options.GrantFileProtocolExtraPrivileges]: false,
  });

  console.log('[FUSES] Electron production security fuses applied successfully:');
  console.log('        - RunAsNode: DISABLED (blocks ELECTRON_RUN_AS_NODE)');
  console.log('        - EnableNodeOptionsEnvironmentVariable: DISABLED (blocks NODE_OPTIONS)');
  console.log('        - EnableNodeCliInspectArguments: DISABLED (blocks --inspect)');
  console.log('        - EnableEmbeddedAsarIntegrityValidation: ENABLED (enforces PE header ASAR hash)');
  console.log('        - OnlyLoadAppFromAsar: ENABLED (enforces app.asar boundary)');
  console.log('        - GrantFileProtocolExtraPrivileges: DISABLED (prevents file:// escalation)');
  console.log('        - EnableCookieEncryption: ENABLED\n');
};
