unit uvisgroupchoice;

{$mode objfpc}{$H+}

interface

uses
  Classes, SysUtils, FileUtil, Forms, Controls, Graphics, Dialogs, StdCtrls,
  ExtCtrls, ButtonPanel, ActnList, sogrid, superobject, LCLType,
  DefaultTranslator, Buttons;

type

  { TvisGroupChoice }

  TvisGroupChoice = class(TForm)
    ActSearch: TAction;
    ActionList1: TActionList;
    ButOK: TBitBtn;
    ButCancel: TBitBtn;
    butSearchGroups: TButton;
    cbBase: TCheckBox;
    cbGroup: TCheckBox;
    cbrestricted: TCheckBox;
    EdSearch: TEdit;
    GridPackages: TSOGrid;
    PanHaut: TPanel;
    Panel2: TPanel;
    panFilter: TPanel;
    panFilter1: TPanel;
    procedure ActSearchExecute(Sender: TObject);
    procedure cbBaseClick(Sender: TObject);
    procedure EdSearchEnter(Sender: TObject);
    procedure EdSearchExit(Sender: TObject);
    procedure EdSearchKeyDown(Sender: TObject; var Key: Word; Shift: TShiftState
      );
    procedure FormClose(Sender: TObject; var CloseAction: TCloseAction);
    procedure FormCreate(Sender: TObject);
    procedure FormShow(Sender: TObject);
  private
    { private declarations }
  public
    { public declarations }
    function SelectedPackages:ISuperObject;
  end;

var
  visGroupChoice: TvisGroupChoice;

implementation

uses dmwaptpython,tisinifiles,tiscommon,VirtualTrees,waptcommon,uWaptPythonUtils;

{$R *.lfm}

{ TvisGroupChoice }

procedure TvisGroupChoice.ActSearchExecute(Sender: TObject);
var
  expr, sections: UTF8String;
  groups : ISuperObject;
begin
  sections := '';
  if cbGroup.Checked then
    sections := sections+',group';
  if cbBase.Checked then
    sections := sections+',base';
  if cbrestricted.Checked then
    sections := sections+',restricted';
  sections := copy(sections,2,255);
  GridPackages.Data := PyVarToSuperObject(DMPython.MainWaptRepo.search(searchwords := EdSearch.Text, sections := sections, newest_only := True,description_locale := Language));
end;

procedure TvisGroupChoice.cbBaseClick(Sender: TObject);
begin
  ActSearch.Execute;
end;

procedure TvisGroupChoice.EdSearchEnter(Sender: TObject);
begin
  ButOK.Default:=False;
end;

procedure TvisGroupChoice.EdSearchExit(Sender: TObject);
begin
  ButOK.Default:=True;
end;

procedure TvisGroupChoice.EdSearchKeyDown(Sender: TObject; var Key: Word;
  Shift: TShiftState);
begin
  if Key = VK_RETURN then
  begin
    EdSearch.SelectAll;
    ActSearch.Execute;
  end
  else
  if Key = VK_DOWN then
  begin
    GridPackages.SetFocus;
  end;
end;

procedure TvisGroupChoice.FormClose(Sender: TObject;
  var CloseAction: TCloseAction);
begin
  IniWriteInteger(Appuserinipath,Name,'Top',Top);
  IniWriteInteger(Appuserinipath,Name,'Left',Left);
  IniWriteInteger(Appuserinipath,Name,'Width',Width);
  IniWriteInteger(Appuserinipath,Name,'Height',Height);
  IniWriteBool(Appuserinipath,Name,'cbGroup.Checked',cbGroup.Checked);
  IniWriteBool(Appuserinipath,Name,'cbBase.Checked',cbBase.Checked);
  IniWriteBool(Appuserinipath,Name,'cbrestricted.Checked',cbrestricted.Checked);
  GridPackages.SaveSettingsToIni(Appuserinipath);
end;

procedure TvisGroupChoice.FormCreate(Sender: TObject);
begin
  if Screen.PixelsPerInch<>96 then
    GridPackages.Header.DefaultHeight:=trunc((GridPackages.Header.DefaultHeight*Screen.PixelsPerInch)/96);
end;

procedure TvisGroupChoice.FormShow(Sender: TObject);
begin
  GridPackages.LoadSettingsFromIni(Appuserinipath);
  Top := IniReadInteger(Appuserinipath,Name,'Top',Top);
  Left := IniReadInteger(Appuserinipath,Name,'Left',Left);
  Width := IniReadInteger(Appuserinipath,Name,'Width',Width);
  Height := IniReadInteger(Appuserinipath,Name,'Height',Height);

  MakeFullyVisible;

  cbGroup.Checked := IniReadBool(Appuserinipath,Name,'cbGroup.Checked',cbGroup.Checked);
  cbBase.Checked := IniReadBool(Appuserinipath,Name,'cbBase.Checked',cbBase.Checked);
  cbrestricted.Checked := IniReadBool(Appuserinipath,Name,'cbrestricted.Checked',cbrestricted.Checked);

  ActSearch.Execute;
end;

function TvisGroupChoice.SelectedPackages: ISuperObject;
var
  N: PVirtualNode;
begin
  Result := TSuperObject.Create(stArray);
  N := GridPackages.GetFirstChecked();
  while N <> nil do
  begin
    Result.AsArray.Add(GridPackages.GetCellStrValue(N, 'package'));
    N := GridPackages.GetNextChecked(N);
  end;
end;

end.

