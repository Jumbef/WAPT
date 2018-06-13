unit uWaptServerRes;

{$mode objfpc}{$H+}

interface

uses
  Classes, SysUtils, DefaultTranslator;

resourcestring

  { --- MESSAGES DANS WAPTSERVER - PostConf --- }
  rsWaptServiceStopping = 'Waptservice stopping';
  rsUpdatingPackageIndex = 'Updating package index';
  rsReplacingTIScertificate = 'Deleting TIS certificate and copying new certificate';
  rsSettingServerPassword = 'Setting up server password';
  rsOpeningFirewall = 'Opening firewall for WaptServer';
  rsRestartingWaptService = 'Restarting waptservice';
  rsRegisteringHostOnServer = 'Registering host on server';
  rsRetryRegisteringHostOnServer = '%D retry on registering host on server';
  rsUpdatingLocalPackages = 'Updating local packages';
  rsConfirm = 'Confirm';
  rsConfirmCancelPostConfig = 'Are you sure you want to cancel configuring WAPT server ?';
  rsInvalidDNS = 'Invalid DNS';
  rsInvalidDNSfallback = 'This DNS name is not valid, would you like to use the IP address instead ?';
  rsMongoDetect = 'Detected Mongodb Base';
  rsRunMongo2Postgresql = 'Do you want to start migrating the mongodb database to postgresql ?';

  rsCreationInProgress = 'Creation in progress.';
  rsProgressTitle = 'Started uploading to WAPT server...';  // TODO more meaningful var name
  rsWaptSetupUploadSuccess = 'WAPT agent successfully created and uploaded to the repository : %s';
  rsWaptUploadError = 'Error while uploading WAPT agent to the repository : %s';
  rsWaptSetupError = 'Error while creating agent : %s';
  rsWaptSetupDone = 'Done';
  rsWaptSetupNext = 'Next';

  rsConfigurePostgreSQL = 'Configure NGINX server and PostgreSQL database';
  rsStoppingPostgreSQL = 'Stopping PostgreSQL';
  rsStoppingNGINX = 'Stopping NGINX';
  rsStoppingWaptServer = 'Stopping WaptServer';
  rsStartingPostgreSQL = 'Starting PostgreSQL';
  rsStartingNGINX = 'Starting NGINX';
  rsStartingWaptServer = 'Starting WaptServer';
  rsCheckingWaptServer = 'Checking WaptServer';

  rsMigration15 = 'Migrate Mongodb to PostgreSQL database';

implementation

end.

