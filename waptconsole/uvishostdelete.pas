unit uVisHostDelete;

{$mode objfpc}{$H+}

interface

uses
  Classes, SysUtils, FileUtil, Forms, Controls, Graphics, Dialogs, ExtCtrls,
  Buttons, StdCtrls;

type

  { TVisHostDelete }

  TVisHostDelete = class(TForm)
    BitBtn1: TBitBtn;
    BitBtn2: TBitBtn;
    CBDeleteHostInventory: TCheckBox;
    CBDeleteHostConfiguration: TCheckBox;
    LabMessage: TLabel;
    Panel1: TPanel;
    Panel2: TPanel;
    procedure FormCreate(Sender: TObject);
  private
    { private declarations }
  public
    { public declarations }
  end;

var
  VisHostDelete: TVisHostDelete;

implementation
{$R *.lfm}

{ TVisHostDelete }

procedure TVisHostDelete.FormCreate(Sender: TObject);
begin
end;

end.

