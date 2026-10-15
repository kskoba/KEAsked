const { notarize } = require('@electron/notarize')
const { execSync } = require('child_process')

exports.default = async function notarizing(context) {
  const { electronPlatformName, appOutDir } = context
  if (electronPlatformName !== 'darwin') return

  // Skip notarization in local builds (no credentials available). In CI a
  // missing credential is a misconfiguration, not a local build: an
  // unnotarized DMG hangs at launch on recent macOS behind a Gatekeeper
  // prompt, so fail the build instead of shipping one.
  const missing = ['APPLE_ID', 'APPLE_APP_SPECIFIC_PASSWORD', 'APPLE_TEAM_ID']
    .filter((name) => !process.env[name])
  if (missing.length) {
    const msg = `notarization credentials not set: ${missing.join(', ')}`
    if (process.env.CI) throw new Error(`Refusing to ship an unnotarized macOS build — ${msg}`)
    console.log(`Skipping notarization — ${msg}.`)
    return
  }

  const appName = context.packager.appInfo.productFilename
  const appPath = `${appOutDir}/${appName}.app`

  console.log(`Notarizing ${appPath} ...`)
  await notarize({
    tool: 'notarytool',
    appPath,
    appleId: process.env.APPLE_ID,
    appleIdPassword: process.env.APPLE_APP_SPECIFIC_PASSWORD,
    teamId: process.env.APPLE_TEAM_ID,
  })

  console.log(`Stapling notarization ticket to ${appPath} ...`)
  execSync(`xcrun stapler staple "${appPath}"`, { stdio: 'inherit' })
}
